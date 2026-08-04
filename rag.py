# -*- coding: utf-8 -*-
"""rag.py — AI 질의 파이프라인.

핵심 원칙 (hallucination 차단, 코드 레벨 강제):
  1. 검색 게이트: 근거 조문이 검색되지 않으면 LLM을 호출하지 않고 거부한다.
  2. 폐쇄형 생성: LLM에는 검색으로 확정된 조문만 제공하고, 그 밖의 지식 사용을 금지한다.
  3. 인용 검증: 답변의 인용 번호가 제공된 조문 집합 안에 있는지 코드로 검증하고,
     벗어난 인용은 제거한다. 유효 인용이 하나도 없으면 답변을 폐기하고 거부한다.

LLM 호출은 OpenAI 호환(chat/completions) — llm_config.json 교체로 사내 API 전환.
"""
import os, re, json, sqlite3, hashlib, struct, urllib.request, urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
LAWS_DB = os.path.join(BASE, "data", "laws.db")
VEC_DB = os.path.join(BASE, "data", "vectors.db")

with open(os.path.join(BASE, "llm_config.json"), encoding="utf-8") as f:
    CFG = json.load(f)

try:
    import numpy as np
except ImportError:
    np = None

_MATRIX = None  # (article_ids: list[int], matrix: np.ndarray 정규화됨)


def _post_json(url, payload, timeout=600):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + CFG.get("api_key", "")})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def llm_available():
    try:
        req = urllib.request.Request(CFG["chat_base_url"] + "/models")
        with urllib.request.urlopen(req, timeout=3):
            return True
    except Exception:
        return False


def embed_query(q):
    data = _post_json(CFG["embed_base_url"] + "/embeddings",
                      {"model": CFG["embed_model"], "input": q}, timeout=120)
    return data["data"][0]["embedding"]


def _load_matrix():
    """laws.db 조문 ↔ vectors.db 임베딩을 매칭해 메모리에 로드(정규화)."""
    global _MATRIX
    if _MATRIX is not None:
        return _MATRIX
    if np is None or not os.path.exists(VEC_DB):
        _MATRIX = ([], None)
        return _MATRIX
    lcon = sqlite3.connect(LAWS_DB)
    vcon = sqlite3.connect(VEC_DB)
    vecs = {h: (blob, dim) for h, blob, dim in vcon.execute(
        "SELECT hash, vec, dim FROM vecs WHERE model=?", (CFG["embed_model"],))}
    ids, mat = [], []
    for aid, name, jo, content in lcon.execute(
            "SELECT a.article_id, l.name, a.jo_label, a.content "
            "FROM articles a JOIN laws l ON l.law_id=a.law_id"):
        text = f"{name} {jo}\n{content[:2500]}"
        h = hashlib.sha1(text.encode("utf-8")).hexdigest()
        v = vecs.get(h)
        if v:
            ids.append(aid)
            mat.append(struct.unpack(f"{v[1]}f", v[0]))
    lcon.close()
    vcon.close()
    if not ids:
        _MATRIX = ([], None)
        return _MATRIX
    m = np.asarray(mat, dtype=np.float32)
    m /= (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)
    _MATRIX = (ids, m)
    return _MATRIX


def invalidate_cache():
    global _MATRIX
    _MATRIX = None


def vector_search(q, k=30):
    ids, m = _load_matrix()
    if m is None or not ids:
        return []
    qv = np.asarray(embed_query(q), dtype=np.float32)
    qv /= (np.linalg.norm(qv) + 1e-9)
    scores = m @ qv
    top = np.argsort(-scores)[:k]
    return [(ids[i], float(scores[i])) for i in top]


def rrf_merge(fts_ids, vec_pairs, k=60):
    """Reciprocal Rank Fusion — 키워드/벡터 랭킹 융합."""
    score = {}
    for rank, aid in enumerate(fts_ids):
        score[aid] = score.get(aid, 0) + 1.0 / (k + rank + 1)
    for rank, (aid, _s) in enumerate(vec_pairs):
        score[aid] = score.get(aid, 0) + 1.0 / (k + rank + 1)
    return sorted(score, key=score.get, reverse=True)


def _fetch_articles(article_ids):
    if not article_ids:
        return []
    con = sqlite3.connect(LAWS_DB)
    con.row_factory = sqlite3.Row
    ph = ",".join("?" * len(article_ids))
    rows = {r["article_id"]: dict(r) for r in con.execute(
        f"SELECT a.article_id, a.jo_label, a.jo_short, a.title, a.chapter,"
        f" a.content, l.name law_name, l.tier, l.kind, l.source_url"
        f" FROM articles a JOIN laws l ON l.law_id=a.law_id"
        f" WHERE a.article_id IN ({ph})", article_ids)}
    con.close()
    return [rows[i] for i in article_ids if i in rows]


SYSTEM_PROMPT = """당신은 한국 안전·환경 법규 검색 어시스턴트입니다. 반도체 소재 제조 사업장 실무자의 질문에 답합니다.

절대 규칙:
1. 아래 [근거 조문]에 명시된 내용만 사용해 답하십시오. 당신의 사전 지식, 추측, 일반 상식은 금지됩니다.
2. 답변의 모든 문장과 체크리스트 항목 끝에는 근거 번호를 [1] [2] 형식으로 붙이십시오.
3. 근거 조문에 없는 내용을 질문받았다면 그 부분은 "제공된 조문으로는 확인 불가"라고 명시하십시오.
4. 반드시 아래 JSON 형식으로만 출력하십시오:
{"answer": "질문에 대한 답변 (각 문장 끝 [n] 인용)", "checklist": ["실무 확인 항목 [n]", "..."], "citations": [사용한 근거 번호 목록]}
5. 답변은 한국어, 간결하게. 확실하지 않으면 checklist를 비우고 answer에 확인 불가라고 쓰십시오."""


def _build_context(articles):
    parts = []
    for i, a in enumerate(articles, 1):
        body = a["content"][:1800]
        parts.append(f"[{i}] {a['law_name']} {a['jo_label']}\n{body}")
    return "\n\n".join(parts)


def _chat(question, context):
    payload = {
        "model": CFG["chat_model"],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content":
                f"[근거 조문]\n{context}\n\n[질문]\n{question}"},
        ],
    }
    data = _post_json(CFG["chat_base_url"] + "/chat/completions", payload)
    return data["choices"][0]["message"]["content"]


def _sources_payload(articles):
    out = []
    for i, a in enumerate(articles, 1):
        if a["kind"] == "kosha":
            pdf = os.path.join(BASE, "bundle", "kosha_pdf", f"{a['jo_short']}.pdf")
            link = ("/kosha_pdf/" + urllib.parse.quote(a["jo_short"]) + ".pdf"
                    if os.path.exists(pdf) else "")
        elif a["kind"] == "sub" or not a["source_url"]:
            link = ""
        elif a["kind"] == "admrul":
            link = a["source_url"]
        else:
            link = a["source_url"] + "/" + urllib.parse.quote(a["jo_short"])
        out.append({"idx": i, "article_id": a["article_id"],
                    "law_name": a["law_name"], "jo_label": a["jo_label"],
                    "tier": a["tier"], "deep_link": link,
                    "excerpt": a["content"][:200]})
    return out


REVIEW_TOPICS = [
    {"id": "register", "label": "① 등록·신고 (화평법/산안법)",
     "q": "{name} 을(를) 신규로 제조 또는 수입하려고 한다. 화학물질 등록, 신고, 유해성·위험성 조사 등 필요한 절차는?"},
    {"id": "permit", "label": "② 유해화학물질 영업허가·취급기준 (화관법)",
     "q": "{name} 이(가) 유해화학물질에 해당하는 경우 영업허가, 취급기준, 취급자 요건은?"},
    {"id": "storage", "label": "③ 보관·저장 시설 기준",
     "q": "{name} 을(를) 창고에 보관·저장하려고 한다. 보관시설·저장시설 설치 기준, 보관량 관련 기준, 검사 의무는? {storage}"},
    {"id": "label", "label": "④ 표시·경고표지·MSDS",
     "q": "{name} 의 용기·포장 표시, 경고표지, 물질안전보건자료(MSDS) 작성·제출·게시 의무는?"},
    {"id": "accident", "label": "⑤ 사고 대비 (예방관리계획·신고)",
     "q": "{name} 취급 시 화학사고예방관리계획서 작성·제출 대상 여부와 화학사고 발생 시 신고 의무는?"},
    {"id": "waste", "label": "⑥ 폐기 (폐수·지정폐기물)",
     "q": "{name} 사용 후 발생하는 폐액·폐수·지정폐기물의 처리 기준과 위탁처리 요건은?"},
]


def build_review_question(topic_id, product):
    t = next((t for t in REVIEW_TOPICS if t["id"] == topic_id), None)
    if not t:
        return None
    name = product.get("name", "").strip()
    cas = product.get("cas", "").strip()
    usage = product.get("usage", "").strip()
    storage = product.get("storage", "").strip()
    nm = name + (f"(CAS {cas})" if cas else "")
    q = t["q"].format(name=nm, storage=f"보관 형태: {storage}." if storage else "")
    if usage:
        q += f" (용도: {usage})"
    return q.strip()


def substance_pseudo_article(substance_rows):
    """물질 등재 행(별표 대조 확정 정보)을 가상 근거 조문으로 변환."""
    lines = []
    seen = set()
    for r in substance_rows[:20]:
        key = (r["law_name"], r["annex"])
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- {r['law_name']} {r['annex']}: {r['context']}")
    return {"article_id": 0, "jo_label": "물질 규제목록 등재 현황",
            "jo_short": "", "title": "별표 대조 확정 정보", "chapter": "",
            "law_name": "규제 목록 대조 결과", "tier": "확정", "kind": "sub",
            "source_url": "",
            "content": "다음은 질문의 물질이 규제 목록(별표)에 등재된 확정 내역이다:\n"
                       + "\n".join(lines)}


def ask(question, fts_results, substance_rows=None):
    """질문 + 키워드 검색결과(app.py에서 전달) → 근거 기반 답변 or 거부."""
    top_k = CFG.get("top_k", 8)

    fts_ids = [r["article_id"] for r in fts_results[:30]]
    try:
        vec_pairs = vector_search(question, k=30)
    except Exception:
        vec_pairs = []

    merged = rrf_merge(fts_ids, vec_pairs)[:top_k]
    best_cos = vec_pairs[0][1] if vec_pairs else 0.0

    # ── 게이트 1: 근거 없음 → LLM 호출 없이 거부 ──
    threshold = CFG.get("refuse_vector_threshold", 0.45)
    if not merged and not substance_rows:
        pass_gate = False
    else:
        pass_gate = bool(fts_ids) or best_cos >= threshold or bool(substance_rows)
    if not pass_gate:
        return {"mode": "refuse",
                "reason": "관련 법령 근거를 찾지 못했습니다. 질문을 법령 용어로 바꿔보시거나, 안전환경팀에 문의하세요.",
                "sources": _sources_payload(_fetch_articles(merged[:5]))}

    articles = _fetch_articles(merged)
    if substance_rows:
        # 물질 확정 정보를 근거 [1]로 고정 — 큰 별표에서 해당 행을 놓치는 문제 방지
        articles = [substance_pseudo_article(substance_rows)] + articles[:top_k - 1]
    context = _build_context(articles)

    try:
        raw = _chat(question, context)
    except Exception as e:
        return {"mode": "error",
                "reason": f"LLM 호출 실패: {e} — ollama serve 실행 여부를 확인하세요.",
                "sources": _sources_payload(articles)}

    try:
        parsed = json.loads(raw)
        answer = str(parsed.get("answer", "")).strip()
        checklist = [str(c) for c in parsed.get("checklist", []) if str(c).strip()]
        cited = parsed.get("citations", [])
    except Exception:
        answer, checklist, cited = raw.strip(), [], []

    # ── 게이트 2: 인용 검증 — 제공한 조문 번호 밖의 인용은 제거 ──
    valid = set(range(1, len(articles) + 1))
    used = set()
    for n in re.findall(r"\[(\d+)\]", answer + " " + " ".join(checklist)):
        used.add(int(n))
    for c in cited:
        try:
            used.add(int(c))
        except (TypeError, ValueError):
            pass
    invalid = used - valid
    used &= valid
    if invalid:  # 존재하지 않는 조문 인용 → 해당 표기는 제거
        bad = "|".join(rf"\[{n}\]" for n in invalid)
        answer = re.sub(bad, "", answer)
        checklist = [re.sub(bad, "", c).strip() for c in checklist]

    # ── 게이트 3: 유효 인용이 전혀 없으면 답변 폐기 ──
    if not used:
        return {"mode": "refuse",
                "reason": "생성된 답변이 검색된 조문을 인용하지 않아 폐기했습니다. 아래 검색된 조문을 직접 확인해 주세요.",
                "sources": _sources_payload(articles)}

    return {"mode": "answer", "answer": answer, "checklist": checklist,
            "cited_idx": sorted(used),
            "sources": _sources_payload(articles)}
