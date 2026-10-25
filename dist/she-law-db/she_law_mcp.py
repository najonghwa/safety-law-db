# -*- coding: utf-8 -*-
"""she_law_mcp.py — 안전법규 DB를 Claude Code의 도구(MCP 서버)로 붙인다. 표준 라이브러리만.

목적은 검색기가 아니라 "클로드가 이 DB를 본 다음 답하게" 하는 것이다. 클로드에게는 도구 다섯 개가
보이고, 법규·인허가·물질 질문이 오면 먼저 gather_evidence 를 불러 근거 조문 묶음을 받은 뒤
그걸 인용하며 자세히 답한다. 답은 질문한 사람의 Claude Code(= 그 사람의 비용)에서 만들어진다.

두 가지 연결 방식 — 설치하는 쪽이 고른다.
  로컬 DB  : 이 파일 옆 data/laws.db 를 직접 읽는다. (사내망 반입 패키지 기본)
  중앙 서버: 환경변수 SHE_LAW_API=http://서버:8777 이 있으면 대시보드(app.py)의 검색 API를 부른다.
            DB 복사가 필요 없고 갱신은 중앙 한 곳에서 한다.

Claude Code 설정(.mcp.json, 이 폴더에 두면 폴더를 열 때 자동 인식):
  {"mcpServers": {"she-law": {"command": "python", "args": ["she_law_mcp.py"]}}}
  중앙 서버 방식이면 "env": {"SHE_LAW_API": "http://10.0.0.5:8777"} 를 추가.

프로토콜: MCP stdio (줄 단위 JSON-RPC 2.0). stdout 에는 JSON 외에 아무것도 쓰지 않는다.
"""
import sys, os, re, json, sqlite3, datetime, urllib.request, urllib.parse

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "data", "laws.db")
SYN = os.path.join(BASE, "synonyms.json")
API = os.environ.get("SHE_LAW_API", "").rstrip("/")
PROTOCOL = "2024-11-05"


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ── 질문 → 검색어 ──────────────────────────────────────────────
_STOP = {"무엇", "무엇이", "무엇인가", "어떤", "어떻게", "어디", "언제", "누가", "왜",
         "필요한가", "필요한지", "필요합니까", "필요해", "필요한", "하나요", "인가요",
         "받으려면", "하려면", "하려는데", "있나요", "있는지", "되나요", "될까요",
         "알려줘", "알려주세요", "궁금합니다", "궁금해", "해야", "하는지", "대해",
         "관련", "경우", "때는", "때에", "그리고", "또는", "우리", "저희", "회사"}
_JOSA = re.compile(r"(에서는|에서도|에서의|으로는|으로도|으로의|에게는|에게도|까지는|까지도|부터는|부터도|"
                   r"에는|에도|에의|로는|로도|와는|과는|이란|란|"
                   r"을|를|이|가|은|는|의|에|에서|으로|로|와|과|도|만|까지|부터|에게|께|"
                   r"이나|나|라도|든지|처럼|보다|마다|조차|밖에)$")
_ENDING = re.compile(r"(하려면|하려는|하려고|해야|하는|한|할|합니다|해도|하고|하면|"
                     r"되는|된|될|시|시에|중인|중|나요|가요|까요|습니까|입니까|인가요|인지|한지|할지)$")
TIMING_Q = re.compile("경과조치|시행일|언제부터|개정 전|개정전|유예|종전|계속 유효|소급|적용례")
FORM_Q = re.compile("서식|신청서|양식|별지|제출서류|첨부서류|작성해야")
TIMING_WORDS = {"개정", "전", "계속", "유효", "유효한", "종전", "이전", "시행", "언제", "적용", "유예", "소급"}
CAS_RE = re.compile(r"\b(\d{2,7}-\d{2}-\d)\b")

_syn = None


def synonyms():
    global _syn
    if _syn is None:
        try:
            with open(SYN, encoding="utf-8") as f:
                _syn = json.load(f).get("synonyms", {})
        except Exception:
            _syn = {}
    return _syn


def keywords(q):
    out = []
    for w in re.split(r"[^\w가-힣]+", q):
        if not w or w in _STOP:
            continue
        base = _ENDING.sub("", _JOSA.sub("", w))
        if len(base) < 2 or base in _STOP or base.isdigit():
            continue
        if base not in out:
            out.append(base)
    return out


# ── 데이터 접근: 로컬 DB 또는 중앙 API ─────────────────────────
def _get_json(path, **params):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def db():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def deep_link(r):
    kind, name, jo = r.get("kind", ""), (r.get("law_name") or "").split(" (시행예정")[0], r.get("jo_short") or ""
    if kind == "kosha":
        return ""
    base = "https://www.law.go.kr/" + urllib.parse.quote(("행정규칙/" if kind == "admrul" else "법령/") + name.replace(" ", ""))
    if kind == "admrul" or not jo or jo.startswith(("부칙", "서식", "별지")):
        return base
    return base + "/" + urllib.parse.quote(jo)


def search(q, tier="", limit=30):
    """검색어 하나 → 결과 목록(dict). 동의어 변형을 합친다."""
    variants = [q]
    for term, subs in synonyms().items():
        if term in q:
            for s in subs:
                v = q.replace(term, s)
                if v not in variants:
                    variants.append(v)
    seen, out = set(), []
    for v in variants[:6]:
        rows = _get_json("/api/search", q=v, tier=tier).get("results", []) if API else _search_local(v, tier, limit)
        for r in rows:
            if r["article_id"] not in seen:
                seen.add(r["article_id"])
                out.append(r)
    return out[:limit]


def _search_local(q, tier, limit):
    con = db()
    terms = [t for t in re.split(r"\s+", q) if t]
    fts_terms = [t for t in terms if len(t) >= 3]
    short = [t for t in terms if 0 < len(t) < 3]
    tier_sql = " AND l.tier=?" if tier else ""
    rows = []
    if fts_terms:
        match = " AND ".join('"' + t.replace('"', "") + '"' for t in fts_terms)
        sql = ("SELECT a.article_id, a.jo_label, a.jo_short, a.title, a.chapter,"
               " l.name law_name, l.tier, l.kind,"
               " snippet(articles_fts, 2, '<<', '>>', ' … ', 26) snip"
               " FROM articles_fts f JOIN articles a ON a.article_id=f.rowid"
               " JOIN laws l ON l.law_id=a.law_id"
               " WHERE articles_fts MATCH ?" + " AND a.content LIKE ?" * len(short) + tier_sql +
               # 부칙·서식은 짧아 bm25를 독식하므로 벌점, KOSHA 지침은 법령 뒤로
               " ORDER BY bm25(articles_fts, 8.0, 4.0, 1.0)"
               " + (CASE WHEN a.jo_short='부칙' THEN 4.0 WHEN a.chapter='서식' THEN 6.0 ELSE 0.0 END)"
               " + (CASE WHEN l.kind='kosha' THEN 2.5 ELSE 0 END) LIMIT ?")
        params = [match] + [f"%{t}%" for t in short] + ([tier] if tier else []) + [limit]
        try:
            rows = [dict(r) for r in con.execute(sql, params).fetchall()]
        except sqlite3.OperationalError:
            rows = []
    if not rows:
        sql = ("SELECT a.article_id, a.jo_label, a.jo_short, a.title, a.chapter,"
               " l.name law_name, l.tier, l.kind, substr(a.content, 1, 160) snip"
               " FROM articles a JOIN laws l ON l.law_id=a.law_id"
               " WHERE a.content LIKE ?" + tier_sql + " LIMIT ?")
        rows = [dict(r) for r in con.execute(sql, [f"%{q}%"] + ([tier] if tier else []) + [limit]).fetchall()]
    con.close()
    return rows


def article(aid):
    if API:
        return _get_json(f"/api/article/{int(aid)}")
    con = db()
    r = con.execute("SELECT a.*, l.name law_name, l.tier, l.kind, l.source_url"
                    " FROM articles a JOIN laws l ON l.law_id=a.law_id WHERE a.article_id=?", (int(aid),)).fetchone()
    con.close()
    return dict(r) if r else None


def _dedup(rows):
    seen, out = set(), []
    for r in rows:
        k = (r.get("law_name"), r.get("annex"), r.get("cas"))
        if k not in seen:
            seen.add(k)
            out.append(dict(r))
    return out


def _sub_query(q):
    """이름 또는 CAS 하나로 조회. 이름은 띄어쓰기를 무시한다(별표 표기 '디메틸 아민')."""
    if API:
        # 대시보드의 물질 조회는 별표 본문까지 넓게 맞추므로(다른 물질 행이 섞인다) 이름·CAS 일치만 남긴다
        rows = _get_json("/api/substance", q=q).get("matches", [])
        nq = q.replace(" ", "")
        tight = [r for r in rows if r.get("cas") == q or nq in (r.get("name") or "").replace(" ", "")]
        return _dedup(tight if tight else rows[:5])
    con = db()
    if CAS_RE.fullmatch(q):
        rows = con.execute("SELECT cas,name,law_name,annex,context FROM substances WHERE cas=?", (q,)).fetchall()
    else:
        nq = q.replace(" ", "")
        if re.search("[가-힣]", nq):
            rows = con.execute("SELECT cas,name,law_name,annex,context FROM substances"
                               " WHERE replace(name,' ','')=? OR replace(name,' ','') LIKE ? LIMIT 40",
                               (nq, f"%{nq}%")).fetchall()
        else:
            # 영문 약어(DMA 등)는 부분일치하면 다른 물질(DMAB)이 걸린다 — 정확히 같은 이름만.
            # 약어 풀이는 클로드가 하고, 확정은 CAS로 다시 조회한다.
            rows = con.execute("SELECT cas,name,law_name,annex,context FROM substances"
                               " WHERE replace(name,' ','')=? LIMIT 40", (nq,)).fetchall()
    con.close()
    return _dedup([dict(r) for r in rows])


def substance_rows(q):
    """물질 등재 현황. 이름으로 물었어도 이름이 정확히 맞는 행의 CAS로 다시 조회해 전체를 모은다 —
    같은 물질이 별표마다 다른 표기('디메틸아민'/'디메틸 아민')로 들어 있어 CAS가 확정 키다."""
    q = q.strip()
    rows = _sub_query(q)
    if not CAS_RE.fullmatch(q):
        nq = q.replace(" ", "")
        cas = next((r["cas"] for r in rows if r.get("cas") and (r.get("name") or "").replace(" ", "") == nq), None)
        if cas:
            rows = _dedup(_sub_query(cas) + rows)
    return rows


def laws_overview(name=""):
    if API:
        groups = _get_json("/api/laws")
        if not name:
            return groups
        for g in groups:
            for l in g["laws"]:
                if name in l["name"]:
                    return _get_json(f"/api/law/{l['law_id']}/articles")
        return {"error": f"'{name}' 에 맞는 법령 없음"}
    con = db()
    if name:
        law = con.execute("SELECT law_id, name, enforce FROM laws WHERE name LIKE ? ORDER BY law_id LIMIT 1",
                          (f"%{name}%",)).fetchone()
        if not law:
            con.close()
            return {"error": f"'{name}' 에 맞는 법령 없음"}
        arts = con.execute("SELECT article_id, jo_label, chapter FROM articles WHERE law_id=?"
                           " AND chapter NOT IN ('별표','서식') AND jo_short!='부칙' ORDER BY jo_no, jo_branch",
                           (law["law_id"],)).fetchall()
        con.close()
        return {"law": dict(law), "articles": [dict(a) for a in arts]}
    rows = con.execute("SELECT grp, name, tier, enforce FROM laws WHERE kind!='kosha' ORDER BY law_id").fetchall()
    con.close()
    out = {}
    for r in rows:
        out.setdefault(r["grp"], []).append(f"{r['name']} ({r['tier']}, 시행 {r['enforce'] or '-'})")
    return out


def db_date():
    try:
        if API:
            stamp = _get_json("/api/stats").get("bundle_created_at", "")[:10]
        else:
            con = db()
            stamp = con.execute("SELECT value FROM meta WHERE key='bundle_created_at'").fetchone()[0][:10]
            con.close()
        days = (datetime.date.today() - datetime.date.fromisoformat(stamp)).days
        warn = (f" ⚠ {days}일 지난 DB — 그 사이 개정분은 미반영. 답변에 이 사실을 밝힐 것." if days > 90 else "")
        return stamp, warn
    except Exception:
        return "?", ""


# ── 근거 묶음 만들기 (핵심 도구) ────────────────────────────────
def rank(r, q):
    tier, label, name = r.get("tier", ""), r.get("jo_label", ""), r.get("law_name", "")
    if tier == "KOSHA" or r.get("kind") == "kosha":
        return 3
    if label.startswith(("서식", "별지")):
        return 0.5 if FORM_Q.search(q) else 2.5
    if label.startswith("별표"):
        return 2
    base = (0.5 if TIMING_Q.search(q) else 1.5) if label.startswith("부칙") else (0 if tier == "법률" else 1)
    return base + (0.25 if "(시행예정" in name else 0)


def gather(question, max_articles=10):
    kws = keywords(question)
    syn = synonyms()
    kws_law = [syn[k][0] if k in syn else k for k in kws]
    combos = []
    if kws_law != kws:
        combos += [kws_law[:i] for i in range(len(kws_law), 0, -1)]
    combos += [kws[:i] for i in range(len(kws), 0, -1)] + [[k] for k in kws]
    if TIMING_Q.search(question):   # 부칙은 "경과조치"라는 말로만 잡힌다
        core = [k for k in kws if k not in TIMING_WORDS and not k.endswith("법")][:2]
        if core:
            combos.insert(0, core + ["경과조치"])
    buckets, uniq, tried = [], {}, set()
    for c in combos or [[question]]:
        key = " ".join(c)
        if key in tried:
            continue
        tried.add(key)
        rows = search(key)
        if rows:
            buckets.append(rows)
            for r in rows:
                uniq.setdefault(r["article_id"], r)
        if sum(1 for r in uniq.values() if r.get("tier") != "KOSHA") >= 40:
            break
    score = {}
    for b in buckets:                       # RRF — 여러 검색어에 함께 잡힌 조문이 위로
        for i, r in enumerate(b):
            score[r["article_id"]] = score.get(r["article_id"], 0) + 1.0 / (60 + i + 1)
    ordered = sorted((uniq[a] for a in score), key=lambda r: (-score[r["article_id"]]))
    ordered = sorted(ordered, key=lambda r: rank(r, question))[:max_articles]

    # 물질 등재 현황 (CAS 또는 물질명)
    subs = []
    for cas in CAS_RE.findall(question):
        subs += substance_rows(cas)
    if not subs:
        for k in kws:
            if len(k) >= 3 and not k.endswith(("법", "허가", "신고", "시설", "기준")):
                rows = substance_rows(k)
                if rows:
                    subs += rows
                    break
    subs = subs[:12]

    stamp, warn = db_date()
    lines = [f"[안전법규 DB 근거 — 기준일 {stamp}]{warn}", ""]
    if subs:
        lines.append("■ 물질 규제 등재 현황 (별표·지정목록 대조 — 확정 정보, CAS로 확인할 것)")
        for s in subs:
            lines.append(f"  - {s.get('law_name','')} | {s.get('annex','')} | CAS {s.get('cas','')} | {str(s.get('context',''))[:160]}")
        lines.append("")
    if not ordered:
        lines.append("■ 관련 조문을 찾지 못했습니다. 법령 용어로 바꿔 search_law 를 다시 시도하거나, 근거 없음을 밝히고 답하십시오.")
    for i, r in enumerate(ordered, 1):
        full = article(r["article_id"]) or {}
        body = (full.get("content") or "").strip()
        if len(body) > 3000:
            body = body[:3000] + " …(이하 생략 — 필요하면 get_article)"
        tag = ""
        lab = r.get("jo_label", "")
        if lab.startswith("부칙"):
            tag = " [부칙 — 시행일·경과조치]"
        elif lab.startswith(("서식", "별지")):
            tag = " [서식 — 신청서 양식]"
        if "(시행예정" in r.get("law_name", "") or "[시행" in lab:
            tag += " [⚠ 시행예정 — 현행 아님]"
        link = full.get("deep_link") or r.get("deep_link") or deep_link({**r, **full})
        lines.append(f"■ [{i}] {r.get('law_name','')} {lab} ({r.get('tier','')}){tag}")
        if full.get("chapter") and not lab.startswith("부칙"):
            lines.append(f"  장절: {full['chapter']}")
        if link:
            lines.append(f"  원문: {link}")
        lines.append("  " + body.replace("\n", "\n  "))
        lines.append("")
    lines.append("※ 답변 규칙: 위 근거만 사용하고 문장마다 [법령명 조]를 붙일 것. 부칙은 적용 시점의 근거, 서식은 양식 항목에만, "
                 "시행예정은 날짜를 밝혀 현행과 구분. 근거에 없는 내용은 '확인 필요'로 분리. 마지막에 DB 기준일과 "
                 "'법적 판단 전 원문(law.go.kr) 확인 필요'를 적을 것.")
    return "\n".join(lines)


# ── MCP 도구 정의 ──────────────────────────────────────────────
INSTRUCTIONS = (
    "이 서버는 한국 안전·보건·환경 법령 DB(화관법·화평법·산안법·중대재해처벌법·위험물법·고압가스법·"
    "폐기물법·대기·물환경·토양·통합환경·소음진동·연구실안전·소방·전기안전·LPG·도시가스 등 82개 법령의 "
    "조문·부칙·별표·서식, 물질(CAS) 규제 목록, KOSHA 지침)를 제공한다.\n"
    "규칙: (1) 법규·인허가·신고·보관·폐기·MSDS·물질 규제처럼 안전·환경 법과 조금이라도 관련된 질문이면 "
    "기억으로 답하지 말고 먼저 gather_evidence 를 호출한다. (2) 받은 근거만 사용해 자세히 답하고 문장마다 "
    "[법령명 제N조] 형식으로 인용한다. (3) 근거에 없는 내용은 '확인 필요'로 분리 표기한다. (4) 물질의 규제 "
    "여부는 check_substance 결과(별표 대조)가 확정 정보다 — 목록에 없다고 규제 없음이라 단정하지 않는다. "
    "(5) 답 끝에 DB 기준일과 '법적 판단 전 원문 확인 필요'를 적는다."
)

TOOLS = [
    {"name": "gather_evidence",
     "description": "질문 하나로 관련 조문·부칙·별표·서식 전문과 물질 등재 현황을 한 번에 모아 준다. "
                    "안전·환경 법규 질문에는 이것을 먼저 호출하고, 결과를 인용하며 답한다.",
     "inputSchema": {"type": "object", "properties": {
         "question": {"type": "string", "description": "사용자 질문 원문(한국어)"},
         "max_articles": {"type": "integer", "description": "가져올 조문 수(기본 10, 최대 20)"}},
         "required": ["question"]}},
    {"name": "search_law",
     "description": "법령 전문 검색(키워드, 공백은 AND). 결과는 조문 요약 목록 — 전문은 get_article.",
     "inputSchema": {"type": "object", "properties": {
         "query": {"type": "string", "description": "법령 용어 위주 검색어, 예: '유해화학물질 영업허가'"},
         "tier": {"type": "string", "description": "법률 / 시행령 / 시행규칙 / 고시 / KOSHA (선택)"},
         "limit": {"type": "integer"}}, "required": ["query"]}},
    {"name": "get_article",
     "description": "조문(또는 별표·서식·부칙) 전문과 원문 링크.",
     "inputSchema": {"type": "object", "properties": {"article_id": {"type": "integer"}}, "required": ["article_id"]}},
    {"name": "check_substance",
     "description": "물질(CAS 번호 또는 이름)의 규제 목록 등재 현황 — 유해화학물질·사고대비·제한·금지, 노출기준, "
                    "관리대상 유해물질 등. CAS 번호로 조회하는 것이 정확하다.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "list_laws",
     "description": "수록 법령 목록(법령군별). 법령명을 주면 그 법령의 조문 목차.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}}},
]


def call_tool(name, args):
    if name == "gather_evidence":
        n = max(1, min(int(args.get("max_articles") or 10), 20))
        return gather(str(args.get("question", "")), n)
    if name == "search_law":
        rows = search(str(args.get("query", "")), str(args.get("tier", "") or ""), int(args.get("limit") or 15))
        stamp, warn = db_date()
        out = [f"검색 {len(rows)}건 (DB 기준일 {stamp}){warn}"]
        for r in rows:
            out.append(f"- article_id {r['article_id']} | {r.get('law_name','')} {r.get('jo_label','')} ({r.get('tier','')})"
                       f"\n    {str(r.get('snip','')).replace(chr(10), ' ')[:200]}")
        return "\n".join(out)
    if name == "get_article":
        a = article(args.get("article_id"))
        if not a:
            return "해당 article_id 없음"
        link = a.get("deep_link") or deep_link(a)
        return (f"{a.get('law_name','')} {a.get('jo_label','')} ({a.get('tier','')})"
                + (f"\n장절: {a['chapter']}" if a.get("chapter") else "") + (f"\n원문: {link}" if link else "")
                + "\n\n" + (a.get("content") or ""))
    if name == "check_substance":
        rows = substance_rows(str(args.get("query", "")))
        if not rows:
            return ("DB 수록 목록에서 확인되지 않음. 수용액·혼합물 함량 기준, 다른 법(고압가스·위험물·산안법) 규제는 "
                    "별개이므로 '규제 없음'으로 단정하지 말 것. CAS 번호로 다시 조회 권장.")
        return "\n".join(f"- {r.get('law_name','')} | {r.get('annex','')} | CAS {r.get('cas','')} | {r.get('name','')}"
                         f"\n    {str(r.get('context',''))[:220]}" for r in rows[:30])
    if name == "list_laws":
        return json.dumps(laws_overview(str(args.get("name", "") or "")), ensure_ascii=False, indent=1)[:20000]
    raise ValueError(f"unknown tool {name}")


# ── JSON-RPC (stdio) 루프 ──────────────────────────────────────
def send(msg):
    sys.stdout.buffer.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def main():
    for raw in sys.stdin.buffer:
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        rid, method, params = req.get("id"), req.get("method", ""), req.get("params") or {}
        try:
            if method == "initialize":
                send({"jsonrpc": "2.0", "id": rid, "result": {
                    "protocolVersion": params.get("protocolVersion") or PROTOCOL,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "she-law", "version": "1.0"},
                    "instructions": INSTRUCTIONS}})
            elif method == "notifications/initialized" or method.startswith("notifications/"):
                pass
            elif method == "ping":
                send({"jsonrpc": "2.0", "id": rid, "result": {}})
            elif method == "tools/list":
                send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
            elif method == "tools/call":
                try:
                    text = call_tool(params.get("name", ""), params.get("arguments") or {})
                    send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": text}], "isError": False}})
                except Exception as e:
                    log("tool error:", repr(e))
                    send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": f"도구 오류: {e}"}], "isError": True}})
            elif rid is not None:
                send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {method}"}})
        except Exception as e:
            log("fatal in loop:", repr(e))
            if rid is not None:
                send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": str(e)}})


if __name__ == "__main__":
    if "--selftest" in sys.argv:    # python she_law_mcp.py --selftest "질문"
        q = sys.argv[-1] if len(sys.argv) > 2 else "유해화학물질 영업허가를 받으려면 무엇이 필요한가?"
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        print(gather(q))
    else:
        main()
