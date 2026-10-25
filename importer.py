# -*- coding: utf-8 -*-
"""importer.py — 폐쇄망(또는 로컬)에서 실행하는 DB 적재기.

bundle/manifest.json 과 bundle/laws/*.html 을 읽어 조(條) 단위로 파싱하고
data/laws.db (SQLite + FTS5 트라이그램 전문검색)를 재생성한다.
외부 네트워크 접근 없음. 표준 라이브러리만 사용.

실행: python importer.py
"""
import sys, io, os, re, csv, json, html as htmlmod, sqlite3

if __name__ == "__main__":  # 모듈로 import될 때 호출측 stdout을 망가뜨리지 않도록
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace", line_buffering=True)

BASE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = os.path.join(BASE, "bundle")
DATA_DIR = os.path.join(BASE, "data")
DB_PATH = os.path.join(DATA_DIR, "laws.db")

# 법령: J{조}:{가지}  /  행정규칙: J{조}-{가지}:{0}
ANCHOR_RE = re.compile(r'<a name="J(\d+)(?:-(\d+))?:(\d+)"')
GTIT_RE = re.compile(r'<p class="gtit"[^>]*>(.*?)</p>', re.S)
LABEL_RE = re.compile(r'<label[^>]*>\s*(제[^<]+?)\s*</label>')
P_RE = re.compile(r'<p[^>]*>(.*?)</p>', re.S)
BABL_RE = re.compile(r'<[^>]*class="babl"[^>]*>(.*?)</', re.S)   # 시행예정 표시 [시행일: …]
TAG_RE = re.compile(r'<[^>]+>')


def strip_tags(fragment):
    frag = re.sub(r'<script.*?</script>', ' ', fragment, flags=re.S)
    frag = re.sub(r'<input[^>]*>', ' ', frag)
    frag = re.sub(r'<img[^>]*>', ' ', frag)
    text = TAG_RE.sub('', frag)
    text = htmlmod.unescape(text)
    return re.sub(r'\s+', ' ', text).strip()


def parse_law_html(raw):
    """법령 본문 HTML → (meta, [article, ...])"""
    meta = {}
    m = re.search(r'\[시행\s*([\d.\s]+?)\s*\]', raw)
    meta["enforce"] = m.group(1).strip() if m else ""
    m = re.search(r'\[([가-힣]+)\s*제[\d-]+호[^\]]*\]', raw)
    meta["proclaim"] = m.group(0).strip("[]") if m else ""

    # 조문 영역: 부칙(arDivArea) 앞까지만
    end = raw.find('id="arDivArea"')
    body = raw[:end] if end > 0 else raw

    # 장/절 제목과 조문 앵커를 문서 순서대로 스캔
    events = []  # (pos, kind, payload)
    for m in GTIT_RE.finditer(body):
        events.append((m.start(), "chapter", strip_tags(m.group(1))))
    for m in ANCHOR_RE.finditer(body):
        events.append((m.start(), "article",
                       (m.group(1), m.group(2), m.group(3))))
    events.sort(key=lambda e: e[0])

    articles, chapter = [], ""
    for i, (pos, kind, payload) in enumerate(events):
        if kind == "chapter":
            chapter = payload
            continue
        g1, g2, g3 = payload
        jo_no = int(g1)
        jo_branch = int(g2) if g2 is not None else int(g3)
        nxt = len(body)
        for p2, k2, _ in events[i + 1:]:
            if k2 == "article" or k2 == "chapter":
                nxt = p2
                break
        section = body[pos:nxt]

        lm = LABEL_RE.search(section)
        jo_label = lm.group(1).strip() if lm else (
            f"제{jo_no}조" + (f"의{jo_branch}" if jo_branch else ""))
        tm = re.match(r'(제\d+조(?:의\d+)?)\s*(?:\((.*?)\))?', jo_label)
        jo_short = tm.group(1) if tm else jo_label
        title = (tm.group(2) or "") if tm else ""
        if 'class="babl"' in section:
            # 시행예정 조문 — 현행 본문에 섞여 있어, 표시하지 않으면 지금 적용되는 규정처럼 보인다
            bm = BABL_RE.search(section)
            tag = strip_tags(bm.group(1)) if bm and strip_tags(bm.group(1)) else "시행예정"
            title = ("[" + tag + "] " + title).strip()
            jo_label = jo_label + " [" + tag + "]"

        lines = []
        for pm in P_RE.finditer(section):
            t = strip_tags(pm.group(1))
            if t:
                lines.append(t)
        content = "\n".join(lines).strip()
        if not content:
            continue
        # 삭제된 조문은 제외하지 않고 그대로 둔다(근거 확인용)
        articles.append({
            "jo_no": jo_no, "jo_branch": jo_branch, "jo_label": jo_label,
            "jo_short": jo_short, "title": title, "chapter": chapter,
            "content": content,
        })
    return meta, articles


# ── 부칙 ──────────────────────────────────────────────────────
# 시행일·경과조치·적용례는 본문이 아니라 부칙(arDivArea)에 있다. "이 법 시행 전에 허가받은
# 자는 ○년까지 …"가 곧 준비 기한인데, 예전 파서는 arDivArea 앞에서 끊어 한 줄도 담지 않았다.
# 법령·고시 모두 같은 구조: 부칙마다 <a name="J…"> 앵커 → pty3 헤더(공포번호·날짜) →
# pty3_dep1(조 시작, <span class="bl">제N조(제목)</span>) / pty3_dep2(이어지는 항).
ADDENDUM_ANCHOR_RE = re.compile(r'<a name="J(\d+)"')
SFON_RE = re.compile(r'<span class="sfon">\s*(.*?)\s*</span>', re.S)
PTY3_RE = re.compile(r'<p class="pty3_dep(\d)"[^>]*>(.*?)</p>', re.S)
BL_RE = re.compile(r'<span class="bl">\s*(제\d+조(?:의\d+)?)\s*(?:\((.*?)\))?\s*</span>\s*(.*)', re.S)
ADDENDUM_BASE = 100000   # 본문(최대 수천)·별표(9000대)와 겹치지 않는 번호대
SKIP_RE = re.compile("(①부터 .{0,30}까지 )?생략[.]?")


def parse_addenda(raw):
    """부칙 영역 → 조문 목록(parse_law_html과 같은 키). 오래된 부칙도 넣는다 —
    경과조치는 수년 뒤까지 효력이 있고, 어느 것이 살아 있는지는 읽는 쪽이 판단한다."""
    start = raw.find('id="arDivArea"')
    if start < 0:
        return []
    area = raw[start:]
    heads = [m.start() for m in ADDENDUM_ANCHOR_RE.finditer(area)]
    out = []
    for bi, bpos in enumerate(heads):
        blk = area[bpos:(heads[bi + 1] if bi + 1 < len(heads) else len(area))]
        sf = [strip_tags(x) for x in SFON_RE.findall(blk)]
        head = sf[0] if sf else ""                     # <법률 제11862호, 2013. 6. 4.>
        other = sf[1] if len(sf) > 1 else ""           # (타법개정이면 그 법률명)
        chapter = ("부칙 " + head + (" " + other if other else "")).strip()
        cur, arts = None, []
        for dep, inner in PTY3_RE.findall(blk):
            m = BL_RE.match(inner.strip())
            if dep == "1" and m:
                if cur:
                    arts.append(cur)
                cur = {"jo_short": m.group(1), "title": m.group(2) or "",
                       "lines": [t for t in [strip_tags(m.group(3))] if t]}
            else:
                t = strip_tags(inner)
                if not t:
                    continue
                if cur is None:                        # 조 번호 없는 한 줄짜리 부칙
                    cur = {"jo_short": "", "title": "", "lines": []}
                cur["lines"].append(t)
        if cur:
            arts.append(cur)
        for n, a in enumerate(arts):
            jm = re.match(r"제(\d+)조(?:의(\d+))?", a["jo_short"])
            jo_no = ADDENDUM_BASE + bi * 100 + (int(jm.group(1)) if jm else 0)
            label = ("부칙 " + a["jo_short"] + ("(" + a["title"] + ")" if a["title"] else "")).strip()                 if a["jo_short"] else "부칙"
            out.append({
                "jo_no": jo_no, "jo_branch": int(jm.group(2)) if jm and jm.group(2) else 0,
                "jo_label": (label + " " + head).strip(),
                "jo_short": "부칙", "title": a["title"], "chapter": chapter,
                "content": chr(10).join(a["lines"]).strip(),
            })
    # "생략"뿐인 조(타법개정 부칙의 "①부터 <382>까지 생략" 등)는 검색에 걸려도 쓸 내용이 없다
    return [a for a in out if a["content"] and not SKIP_RE.fullmatch(a["content"])]


def main():
    manifest_path = os.path.join(BUNDLE, "manifest.json")
    if not os.path.exists(manifest_path):
        print("bundle/manifest.json 이 없습니다. 먼저 collector.py를 실행(또는 번들 반입)하세요.")
        sys.exit(1)
    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.executescript("""
    CREATE TABLE laws(
      law_id INTEGER PRIMARY KEY,
      name TEXT UNIQUE, grp TEXT, tier TEXT, kind TEXT, ministry TEXT,
      lsi_seq TEXT, ef_yd TEXT, enforce TEXT, proclaim TEXT,
      source_url TEXT, fetched_at TEXT, article_count INTEGER
    );
    CREATE TABLE articles(
      article_id INTEGER PRIMARY KEY,
      law_id INTEGER REFERENCES laws(law_id),
      jo_no INTEGER, jo_branch INTEGER,
      jo_label TEXT, jo_short TEXT, title TEXT, chapter TEXT, content TEXT
    );
    CREATE VIRTUAL TABLE articles_fts USING fts5(
      jo_label, title, content,
      tokenize='trigram', content='articles', content_rowid='article_id'
    );
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE substances(
      id INTEGER PRIMARY KEY,
      cas TEXT, name TEXT, law_name TEXT, annex TEXT, context TEXT
    );
    CREATE INDEX idx_sub_cas ON substances(cas);
    """)
    cur.execute("INSERT INTO meta VALUES('bundle_created_at', ?)",
                (manifest.get("created_at", ""),))

    total_articles = 0
    print(f"{'법령명':<42} {'조문수':>5}  시행일")
    print("-" * 70)
    for it in manifest["items"]:
        path = os.path.join(BUNDLE, it["file"])
        if not os.path.exists(path):
            print(f"! 파일 없음: {it['file']}")
            continue
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        meta, articles = parse_law_html(raw)
        articles += parse_addenda(raw)   # 부칙(시행일·경과조치·적용례) — 준비 기한이 여기 있다
        cur.execute(
            "INSERT INTO laws(name,grp,tier,kind,ministry,lsi_seq,ef_yd,enforce,"
            "proclaim,source_url,fetched_at,article_count) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (it["name"], it["group"], it["tier"], it.get("kind", "law"),
             it["ministry"], it["lsi_seq"],
             it["ef_yd"], meta["enforce"], meta["proclaim"], it["source_url"],
             it["fetched_at"], len(articles)))
        law_id = cur.lastrowid
        for a in articles:
            cur.execute(
                "INSERT INTO articles(law_id,jo_no,jo_branch,jo_label,jo_short,"
                "title,chapter,content) VALUES(?,?,?,?,?,?,?,?)",
                (law_id, a["jo_no"], a["jo_branch"], a["jo_label"], a["jo_short"],
                 a["title"], a["chapter"], a["content"]))
            cur.execute(
                "INSERT INTO articles_fts(rowid,jo_label,title,content) VALUES(?,?,?,?)",
                (cur.lastrowid, a["jo_label"], a["title"], a["content"]))
        total_articles += len(articles)
        print(f"{it['name']:<42} {len(articles):>5}  {meta['enforce']}")

    kosha_n = import_kosha(cur)
    annex_n, sub_n = import_annexes(cur)
    if annex_n:
        print(f"별표 {annex_n}건 적재, 물질(CAS) 항목 {sub_n}건 추출")
    icis_n = import_icis_substances(cur)
    if icis_n:
        print(f"화학물질 규제 목록(ICIS) {icis_n}건 적재")
    con.commit()
    con.close()
    print("-" * 70)
    print(f"합계: 법령 {len(manifest['items'])}건, 조문 {total_articles}개"
          + (f", KOSHA Guide {kosha_n}건" if kosha_n else ""))
    print(f"DB: {DB_PATH}")


def _try_import(name):
    try:
        __import__(name)
        return True
    except Exception:
        return False


CAS_RE = re.compile(r"\b(\d{2,7}-\d{2}-\d)\b")


def cas_valid(cas):
    """CAS 번호 체크섬 검증 (오탐 감소)."""
    digits = cas.replace("-", "")
    body, check = digits[:-1], int(digits[-1])
    s = sum(int(d) * (i + 1) for i, d in enumerate(reversed(body)))
    return s % 10 == check


def pdf_text(path):
    """PDF → 텍스트. pymupdf 우선, 실패 시 pypdf(순수 파이썬) 폴백."""
    try:
        import pymupdf
        doc = pymupdf.open(path)
        text = "\n".join(p.get_text() for p in doc)
        doc.close()
        return text
    except Exception:
        pass
    try:
        from pypdf import PdfReader
        return "\n".join(p.extract_text() or "" for p in PdfReader(path).pages)
    except Exception:
        return ""


def clean_text(t):
    """SQLite 저장 전 정리.

    HWP 파싱 결과에는 NULL(\\x00)과 제어문자가 섞여 있는데, NULL이 들어가면
    SQLite가 그 지점에서 문자열을 잘라버린다(별표 본문이 통째로 사라지는 원인).
    """
    if not t:
        return ""
    t = t.replace("\x00", " ")
    t = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip()


def extract_annex_text(path):
    """HWP/HWPX/PDF → 텍스트. 라이브러리 없으면 빈 문자열.

    원본이 그림뿐이라 글자가 거의 안 나오면 ocr_scans.py가 만든 사이드카(.ocr.txt)를 쓴다.
    (소량 취급시설 판단방법 흐름도 같은 것 — 2026-10 전수 조사에서 그림 별표 2건)
    """
    text = _extract_annex_text(path)
    side = path + ".ocr.txt"
    if len(text) < 300 and os.path.exists(side):
        with open(side, encoding="utf-8", errors="replace") as f:
            ocr = clean_text(f.read())
        if len(ocr) > len(text):
            return "(그림 별표 — OCR로 읽은 텍스트. 수치·기호는 원본 그림을 확인할 것)" + chr(10) + ocr
    return text


def _extract_annex_text(path):
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".txt":
            with open(path, encoding="utf-8", errors="replace") as f:
                return clean_text(f.read())
        if ext == ".pdf":
            return clean_text(pdf_text(path))
        if ext in (".hwp", ".hwpx"):
            import gethwp
            raw = gethwp.read_hwp(path) if ext == ".hwp" else gethwp.read_hwpx(path)
            return clean_text(raw)
    except Exception:
        return ""
    return ""


def import_annexes(cur):
    """bundle/annex_manifest.json + annex/* → 별표 텍스트를 조문으로 적재하고
    CAS 번호가 포함된 행을 substances 테이블로 추출한다."""
    mpath = os.path.join(BUNDLE, "annex_manifest.json")
    if not os.path.exists(mpath):
        return 0, 0
    with open(mpath, encoding="utf-8") as f:
        annexes = json.load(f).get("annexes", [])

    law_ids = {name: lid for lid, name in cur.execute(
        "SELECT law_id, name FROM laws").fetchall()}

    n_annex = n_sub = 0
    for a in annexes:
        law_id = law_ids.get(a["law_name"])
        if not law_id:
            continue
        path = os.path.join(BUNDLE, a["file"])
        if not os.path.exists(path):
            continue
        text = extract_annex_text(path)
        # 긴 별표(물질 목록 등 수십만 자)는 잘라내지 말고 청크로 나눠 전부 적재
        CH = 4000
        chunks = [text[i:i + CH] for i in range(0, len(text), CH)] or [""]
        for ci, chunk in enumerate(chunks):
            label = a["label"] if len(chunks) == 1 \
                else f"{a['label']} ({ci+1}/{len(chunks)})"
            content = f"{a['label']} {a['title']}\n{chunk}".strip()
            cur.execute(
                "INSERT INTO articles(law_id,jo_no,jo_branch,jo_label,jo_short,"
                "title,chapter,content) VALUES(?,?,?,?,?,?,?,?)",
                (law_id, 9000 + n_annex, ci, label, a["label"],
                 a["title"], "서식" if a["label"].startswith(("서식", "별지")) else "별표", content))
            cur.execute(
                "INSERT INTO articles_fts(rowid,jo_label,title,content) VALUES(?,?,?,?)",
                (cur.lastrowid, label, a["title"], content))
        n_annex += 1

        # 물질(CAS) 추출 — CAS 주변 문맥을 함께 저장(수량 기준·물질명 보존)
        flat = re.sub(r"\s+", " ", text)   # 개행까지 공백으로 (물질명 추출 시 줄바뀜 방지)
        seen_cas = set()
        for m in CAS_RE.finditer(flat):
            cas = m.group(1)
            if not cas_valid(cas) or cas in seen_cas:
                continue
            seen_cas.add(cas)
            # 앞쪽 120자에서 물질명, 뒤쪽 90자까지 포함해 문맥 확보
            left = flat[max(0, m.start() - 120):m.start()]
            right = flat[m.end():m.end() + 90]
            # 별표 표기 패턴 2종을 우선 인식:
            #   A) "디메틸 아민 [Dimethylamine] 124-40-3"   (규정수량·지정 고시류)
            #   B) "디메틸아민(Dimethylamine; 124-40-3)"     (산안법 별표류)
            name = ""
            mm = re.search(r"([가-힣][가-힣0-9,\-()·ㆍ%\s]{1,58})\s*\[[^\[\]]{1,90}\]\s*$", left)
            if not mm:
                mm = re.search(r"([가-힣][가-힣0-9,\-·ㆍ%\s]{1,48})\(\s*[A-Za-z][^;()]{0,70};\s*$", left)
            if mm:
                name = mm.group(1)
            else:  # 폴백: 직전 텍스트에서 한글 시작 구간
                name = re.sub(r"[\d.\s|()\[\]:;·ㆍ]+$", "", left).strip()
                name = re.sub(r"^.*?(?=[가-힣A-Za-z][^,]{0,58}$)", "", name)[-60:]
            name = re.sub(r"^[\s\d.)>│|·ㆍ\-]+", "", name).strip()[:60]
            context = re.sub(r"\s+", " ", (left[-90:] + cas + right)).strip()
            cur.execute(
                "INSERT INTO substances(cas,name,law_name,annex,context)"
                " VALUES(?,?,?,?,?)",
                (cas, name, a["law_name"], f"{a['label']} {a['title']}",
                 context[:300]))
            n_sub += 1
    return n_annex, n_sub


def import_icis_substances(cur):
    """bundle/substances_icis.json → substances 테이블.

    유해화학물질(인체등유해성물질)·제한·금지·사고대비·허가물질 지정 목록.
    국가법령정보센터가 해당 지정고시 별표를 파일로 주지 않아 ICIS에서 별도 수집한다.
    """
    path = os.path.join(BUNDLE, "substances_icis.json")
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    src = doc.get("source", "화학물질종합정보시스템(ICIS)")
    n = 0
    for it in doc.get("items", []):
        cas = (it.get("cas") or "").strip()
        name = (it.get("name_ko") or "").strip()
        if not cas and not name:
            continue
        cat = it.get("category", "")
        law = {"인체등유해성물질": "화학물질관리법(유해화학물질)",
               "제한물질": "화학물질관리법(제한물질)",
               "금지물질": "화학물질관리법(금지물질)",
               "사고대비물질": "화학물질관리법(사고대비물질)",
               "허가물질": "화학물질관리법(허가물질)"}.get(cat, "화학물질관리법")
        eng = it.get("name_en") or ""
        no = it.get("no")
        context = f"{cat} 지정 — {name}" + (f" ({eng})" if eng else "") \
                  + (f" [고시번호 {no}]" if no else "") + f" · 출처: {src}"
        cur.execute("INSERT INTO substances(cas,name,law_name,annex,context)"
                    " VALUES(?,?,?,?,?)",
                    (cas, name, law, f"{cat} 지정 목록", context))
        n += 1
    return n


def import_kosha(cur):
    """bundle/kosha_list.csv → 분류별 그룹으로 지침 목록 적재.
    bundle/kosha_pdf/{지침번호}.pdf 가 있으면(선택) 원문 텍스트도 함께 적재."""
    path = os.path.join(BUNDLE, "kosha_list.csv")
    if not os.path.exists(path):
        return 0
    pdf_dir = os.path.join(BUNDLE, "kosha_pdf")
    # PDF 텍스트 추출 가능 여부 확인
    has_pdf_lib = bool(pdf_text.__code__) and (
        _try_import("pymupdf") or _try_import("pypdf"))

    with open(path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    # 공공데이터포털의 목록 CSV는 2023-11에서 멈춰 있다. 조회 API(kosha_api.json)에만 있는
    # 2024~2026년 신규 지침을 보태지 않으면 가장 최신 지침이 DB에서 빠진다(2026-10 확인: 237건).
    api_path = os.path.join(BUNDLE, "kosha_api.json")
    if os.path.exists(api_path):
        have = {r["지침번호"] for r in rows}
        desc_by_code = {r["분류기호"]: r["분류내용"] for r in rows}
        seq = max((int(r["연번"]) for r in rows if str(r.get("연번", "")).isdigit()), default=0)
        with open(api_path, encoding="utf-8") as f:
            added = 0
            for it in json.load(f):
                no = it.get("no", "")
                if not no or no in have:
                    continue
                parts = no.split("-")
                if len(parts) >= 3 and parts[1] == "G":        # A-G-3-2025: 기술지원규정
                    code, desc = parts[0] + "-G", "기술지원규정"
                else:
                    code = parts[0]
                    desc = desc_by_code.get(code, "기타")
                seq += 1
                rows.append({"연번": str(seq), "위원회": "", "등록일": it.get("date", ""),
                             "분류기호": code, "분류내용": desc, "공표순": "",
                             "년도": parts[-1], "지침번호": no, "명칭": it.get("title", "")})
                have.add(no)
                added += 1
        if added:
            print(f"KOSHA 조회 API에서 목록 CSV에 없는 지침 {added}건 보탬")

    groups = {}  # 분류기호 → [row]
    for r in rows:
        groups.setdefault((r["분류기호"], r["분류내용"]), []).append(r)

    total = 0
    for (code, desc), items in sorted(groups.items()):
        cur.execute(
            "INSERT INTO laws(name,grp,tier,kind,ministry,lsi_seq,ef_yd,enforce,"
            "proclaim,source_url,fetched_at,article_count) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"KOSHA Guide {code} ({desc})", "KOSHA Guide", "KOSHA", "kosha",
             "한국산업안전보건공단", "", "", "", "",
             "https://www.kosha.or.kr/kosha/data/guidanceP.do", "", len(items)))
        law_id = cur.lastrowid
        for r in items:
            no, title = r["지침번호"], r["명칭"]
            head = (f"{no} {title}\n분류: {desc} / 위원회: {r['위원회']}"
                    f" / 등록일: {r['등록일']}")
            pdf = os.path.join(pdf_dir, f"{no}.pdf")
            fulltext = ""
            if has_pdf_lib and os.path.exists(pdf):
                fulltext = clean_text(pdf_text(pdf))[:40000]
            if len(fulltext.strip()) < 200:      # 스캔본 — ocr_scans.py 사이드카가 있으면 사용
                side = os.path.join(BUNDLE, "kosha_ocr", f"{no}.txt")
                if os.path.exists(side):
                    with open(side, encoding="utf-8", errors="replace") as sf:
                        fulltext = ("(스캔본 — OCR로 읽은 텍스트) " + clean_text(sf.read()))[:40000]
            # PDF 본문은 ~1800자 청크로 분할 (검색·RAG 품질), 목차행만 있으면 1건
            chunks = [head] if not fulltext else \
                [head + "\n" + fulltext[i:i + 1800]
                 for i in range(0, min(len(fulltext), 18000), 1800)]
            for ci, content in enumerate(chunks):
                label = no if len(chunks) == 1 else f"{no} ({ci+1}/{len(chunks)})"
                cur.execute(
                    "INSERT INTO articles(law_id,jo_no,jo_branch,jo_label,jo_short,"
                    "title,chapter,content) VALUES(?,?,?,?,?,?,?,?)",
                    (law_id, int(r["연번"]), ci, label, no, title, desc, content))
                cur.execute(
                    "INSERT INTO articles_fts(rowid,jo_label,title,content)"
                    " VALUES(?,?,?,?)",
                    (cur.lastrowid, label, title, content))
            total += 1
    print(f"KOSHA Guide 목록 {total}건 적재 (분류 {len(groups)}개"
          + (", PDF 원문 포함" if has_pdf_lib else ", 목록만 — PDF 라이브러리 없음") + ")")
    return total


if __name__ == "__main__":
    main()
