# -*- coding: utf-8 -*-
"""app.py — 안전법규 DB 웹 UI 서버 (표준 라이브러리만 사용, 오프라인 동작).

실행:  python app.py            → http://localhost:8777
사내망 공유: python app.py --host 0.0.0.0
"""
import sys, io, os, re, json, sqlite3, urllib.parse, threading, webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "data", "laws.db")
WEB_DIR = os.path.join(BASE, "web")
KOSHA_PDF_DIR = os.path.join(BASE, "bundle", "kosha_pdf")
PORT = 8777


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def api_stats():
    con = db()
    laws = con.execute("SELECT COUNT(*) c FROM laws").fetchone()["c"]
    arts = con.execute("SELECT COUNT(*) c FROM articles").fetchone()["c"]
    grps = con.execute("SELECT COUNT(DISTINCT grp) c FROM laws").fetchone()["c"]
    bundle = con.execute(
        "SELECT value FROM meta WHERE key='bundle_created_at'").fetchone()
    con.close()
    return {"laws": laws, "articles": arts, "groups": grps,
            "bundle_created_at": bundle["value"] if bundle else ""}


def api_laws():
    con = db()
    rows = con.execute(
        "SELECT law_id,name,grp,tier,ministry,enforce,proclaim,source_url,"
        "article_count FROM laws ORDER BY law_id").fetchall()
    con.close()
    groups, order = {}, []
    for r in rows:
        g = r["grp"]
        if g not in groups:
            groups[g] = {"group": g, "ministry": r["ministry"], "laws": []}
            order.append(g)
        groups[g]["laws"].append(dict(r))
    return [groups[g] for g in order]


def api_articles(law_id):
    con = db()
    rows = con.execute(
        "SELECT article_id,jo_label,jo_short,title,chapter FROM articles "
        "WHERE law_id=? ORDER BY jo_no, jo_branch", (law_id,)).fetchall()
    law = con.execute("SELECT name,source_url FROM laws WHERE law_id=?",
                      (law_id,)).fetchone()
    con.close()
    return {"law": dict(law) if law else None, "articles": [dict(r) for r in rows]}


def api_article(article_id):
    con = db()
    r = con.execute(
        "SELECT a.*, l.name law_name, l.tier, l.kind, l.source_url FROM articles a "
        "JOIN laws l ON l.law_id=a.law_id WHERE a.article_id=?",
        (article_id,)).fetchone()
    con.close()
    if not r:
        return None
    d = dict(r)
    d["deep_link"] = _deep_link(r["source_url"], r["kind"], r["jo_short"])
    return d


def _deep_link(source_url, kind, jo_short):
    if kind == "kosha":
        # KOSHA Guide는 로컬 보관 PDF를 직접 서빙 (공단 사이트 개편으로 외부 링크 불안정)
        if os.path.exists(os.path.join(KOSHA_PDF_DIR, f"{jo_short}.pdf")):
            return "/kosha_pdf/" + urllib.parse.quote(jo_short) + ".pdf"
        return ""
    # 행정규칙은 law.go.kr 조 단위 딥링크 미지원 → 문서 링크만
    if kind == "admrul":
        return source_url
    return source_url + "/" + urllib.parse.quote(jo_short)


_SYN = None


def load_synonyms():
    global _SYN
    if _SYN is None:
        try:
            with open(os.path.join(BASE, "synonyms.json"), encoding="utf-8") as f:
                _SYN = json.load(f)["synonyms"]
        except Exception:
            _SYN = {}
    return _SYN


def expand_query(q):
    """현장 용어를 법령 용어로 치환한 변형 쿼리 목록 생성."""
    variants, notes = [q], []
    for term, subs in load_synonyms().items():
        if term in q:
            notes.append({"term": term, "to": subs})
            for s in subs:
                v = q.replace(term, s)
                if v not in variants:
                    variants.append(v)
    return variants[:8], notes


def api_search(q, tier="", limit=60):
    q = (q or "").strip()
    if not q:
        return {"query": q, "results": []}
    variants, notes = expand_query(q)
    seen, merged = set(), []
    for v in variants:
        for r in _search_one(v, tier, limit):
            if r["article_id"] not in seen:
                seen.add(r["article_id"])
                r["matched_query"] = v
                merged.append(r)
    return {"query": q, "results": merged[:limit], "expansions": notes}


def _search_one(q, tier="", limit=60):
    con = db()
    params, tier_sql = [], ""
    if tier:
        tier_sql = " AND l.tier=?"

    results = []
    terms = [t for t in re.split(r"\s+", q) if t]
    fts_terms = [t for t in terms if len(t) >= 3]     # 트라이그램은 3자 이상만 매칭 가능
    short_terms = [t for t in terms if 0 < len(t) < 3]
    if fts_terms:
        match = " AND ".join('"' + t.replace('"', "") + '"' for t in fts_terms)
        short_sql = " AND a.content LIKE ?" * len(short_terms)
        sql = ("SELECT a.article_id, a.jo_label, a.jo_short, a.title, a.chapter,"
               " l.name law_name, l.tier, l.kind, l.grp, l.source_url,"
               " snippet(articles_fts, 2, '[[', ']]', ' … ', 28) snip"
               " FROM articles_fts f"
               " JOIN articles a ON a.article_id = f.rowid"
               " JOIN laws l ON l.law_id = a.law_id"
               " WHERE articles_fts MATCH ?" + short_sql + tier_sql +
               " ORDER BY bm25(articles_fts, 8.0, 4.0, 1.0)"
               "  + (CASE WHEN l.kind='kosha' THEN 2.5 ELSE 0 END) LIMIT ?")
        params = [match] + [f"%{t}%" for t in short_terms] \
            + ([tier] if tier else []) + [limit]
        try:
            results = [dict(r) for r in con.execute(sql, params).fetchall()]
        except sqlite3.OperationalError:
            results = []
    if not results:  # 짧은 검색어 또는 FTS 무결과 → LIKE 폴백
        sql = ("SELECT a.article_id, a.jo_label, a.jo_short, a.title, a.chapter,"
               " l.name law_name, l.tier, l.kind, l.grp, l.source_url,"
               " substr(a.content, max(1, instr(a.content, ?) - 60), 160) snip"
               " FROM articles a JOIN laws l ON l.law_id=a.law_id"
               " WHERE a.content LIKE ?" + tier_sql + " LIMIT ?")
        params = [q, f"%{q}%"] + ([tier] if tier else []) + [limit]
        results = [dict(r) for r in con.execute(sql, params).fetchall()]
        for r in results:
            r["snip"] = r["snip"].replace(q, f"[[{q}]]")
    con.close()
    for r in results:
        r["deep_link"] = _deep_link(r["source_url"], r["kind"], r["jo_short"])
    return results


def api_substance(q):
    """CAS 또는 물질명으로 규제 목록(별표 등재 현황) 조회."""
    q = (q or "").strip()
    if not q:
        return {"query": q, "matches": []}
    con = db()
    if re.fullmatch(r"\d{2,7}-\d{2}-\d", q):
        rows = con.execute(
            "SELECT cas,name,law_name,annex,context FROM substances WHERE cas=?",
            (q,)).fetchall()
    else:
        rows = con.execute(
            "SELECT cas,name,law_name,annex,context FROM substances"
            " WHERE name LIKE ? OR context LIKE ? LIMIT 100",
            (f"%{q}%", f"%{q}%")).fetchall()
    # (법령, 별표) 단위로 묶고, 해당 별표 조문으로 바로 갈 수 있게 article_id 연결
    seen, matches = set(), []
    for r in rows:
        key = (r["law_name"], r["annex"], r["cas"])
        if key in seen:
            continue
        seen.add(key)
        d = dict(r)
        label = (r["annex"] or "").split(" ")[0]  # "별표12 제목…" → "별표12"
        art = con.execute(
            "SELECT a.article_id FROM articles a JOIN laws l ON l.law_id=a.law_id"
            " WHERE l.name=? AND a.jo_short=? ORDER BY a.jo_branch LIMIT 1",
            (r["law_name"], label)).fetchone()
        d["article_id"] = art["article_id"] if art else None
        matches.append(d)
    con.close()
    return {"query": q, "matches": matches}


CAS_RE = re.compile(r"\b(\d{2,7}-\d{2}-\d)\b")


def cas_valid(cas):
    digits = cas.replace("-", "")
    return sum(int(d) * (i + 1)
               for i, d in enumerate(reversed(digits[:-1]))) % 10 == int(digits[-1])


def find_substance_rows(*terms):
    """질문/제품정보에서 물질을 찾아 등재 행 반환 (CAS 우선, 이름 부분일치 보조)."""
    con = db()
    rows, seen = [], set()

    def add(rs):
        for r in rs:
            k = (r["law_name"], r["annex"], r["context"][:80])
            if k not in seen:
                seen.add(k)
                rows.append(dict(r))

    for t in terms:
        t = (t or "").strip()
        if not t:
            continue
        for cas in CAS_RE.findall(t):
            if cas_valid(cas):
                add(con.execute("SELECT law_name,annex,context FROM substances"
                                " WHERE cas=?", (cas,)))
        for tok in re.split(r"\s+", t):
            tok = re.sub(r"(의|을|를|이|가|은|는|과|와|으로|로)$", "", tok)
            if len(tok) >= 3 and not CAS_RE.search(tok):
                add(con.execute("SELECT law_name,annex,context FROM substances"
                                " WHERE name LIKE ? LIMIT 20", (f"%{tok}%",)))
    con.close()
    return rows[:20]


def api_msds(filename, data_b64):
    """MSDS PDF 업로드 → 텍스트 추출 → CAS·물질명 인식 → 규제 매칭."""
    import base64, io as _io
    raw = base64.b64decode(data_b64)
    text = None
    try:
        import pymupdf
        doc = pymupdf.open(stream=raw, filetype="pdf")
        text = "\n".join(p.get_text() for p in doc)
        doc.close()
    except Exception:
        pass
    if text is None:
        try:
            from pypdf import PdfReader
            text = "\n".join(p.extract_text() or ""
                             for p in PdfReader(_io.BytesIO(raw)).pages)
        except Exception as e:
            return {"error": f"PDF를 읽을 수 없습니다: {e} (pip install pypdf 필요)"}

    cas_found = []
    for cas in dict.fromkeys(CAS_RE.findall(text)):  # 순서 유지 중복 제거
        if cas_valid(cas):
            cas_found.append(cas)

    # 제품명 추정: '제품명' 라벨 주변 또는 첫 유의미 행
    name = ""
    m = re.search(r"제\s*품\s*명\s*[:：]?\s*([^\n]{2,60})", text)
    if m:
        name = m.group(1).strip()

    results = []
    for cas in cas_found[:20]:
        matches = api_substance(cas)["matches"]
        results.append({"cas": cas, "matches": matches})
    return {"filename": filename, "product_name": name,
            "cas_list": results, "text_chars": len(text)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/api/ask":
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                q = (body.get("q") or "").strip()
                if not q:
                    self.send_json({"error": "질문이 비어 있습니다"}, 400)
                    return
                import rag
                fts = api_search(q).get("results", [])
                subs = find_substance_rows(q)
                self.send_json(rag.ask(q, fts, subs))
            elif parsed.path == "/api/msds":
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                self.send_json(api_msds(body.get("filename", ""),
                                        body.get("data", "")))
            elif parsed.path == "/api/review":
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                import rag
                q = rag.build_review_question(body.get("topic", ""),
                                              body.get("product", {}))
                if not q:
                    self.send_json({"error": "unknown topic"}, 400)
                    return
                product = body.get("product", {})
                fts = api_search(q).get("results", [])
                subs = find_substance_rows(product.get("name", ""),
                                           product.get("cas", ""))
                result = rag.ask(q, fts, subs)
                result["question"] = q
                self.send_json(result)
            else:
                self.send_json({"error": "not found"}, 404)
        except BrokenPipeError:
            pass
        except Exception as e:
            self.send_json({"error": str(e)}, 500)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        try:
            if path == "/":
                path = "/index.html"
            if path.startswith("/kosha_pdf/"):
                name = urllib.parse.unquote(path[len("/kosha_pdf/"):])
                if not re.fullmatch(r"[\w가-힣.·-]+\.pdf", name):
                    self.send_json({"error": "bad name"}, 400)
                    return
                fpath = os.path.join(KOSHA_PDF_DIR, name)
                if not os.path.exists(fpath):
                    self.send_json({"error": "not found"}, 404)
                    return
                with open(fpath, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Disposition",
                                 "inline; filename*=UTF-8''" + urllib.parse.quote(name))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if re.fullmatch(r"/[\w가-힣.-]+\.(html|css|js|png|svg|woff2)", path):
                fpath = os.path.join(WEB_DIR, path.lstrip("/"))
                if not os.path.exists(fpath):
                    self.send_json({"error": "not found"}, 404)
                    return
                ctype = {"html": "text/html", "css": "text/css",
                         "js": "text/javascript", "png": "image/png",
                         "svg": "image/svg+xml",
                         "woff2": "font/woff2"}[path.rsplit(".", 1)[1]]
                with open(fpath, "rb") as f:
                    body = f.read()
                self.send_response(200)
                self.send_header("Content-Type", f"{ctype}; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/stats":
                self.send_json(api_stats())
            elif path == "/api/laws":
                self.send_json(api_laws())
            elif path.startswith("/api/law/"):
                law_id = int(path.split("/")[3])
                self.send_json(api_articles(law_id))
            elif path.startswith("/api/article/"):
                d = api_article(int(path.split("/")[3]))
                self.send_json(d if d else {"error": "not found"},
                               200 if d else 404)
            elif path == "/api/substance":
                self.send_json(api_substance(qs.get("q", [""])[0]))
            elif path == "/api/review_topics":
                import rag
                self.send_json([{"id": t["id"], "label": t["label"]}
                                for t in rag.REVIEW_TOPICS])
            elif path == "/api/search":
                q = qs.get("q", [""])[0]
                tier = qs.get("tier", [""])[0]
                self.send_json(api_search(q, tier))
            else:
                self.send_json({"error": "not found"}, 404)
        except BrokenPipeError:
            pass
        except Exception as e:
            self.send_json({"error": str(e)}, 500)


def main():
    host = "127.0.0.1"
    if "--host" in sys.argv:
        host = sys.argv[sys.argv.index("--host") + 1]
    if not os.path.exists(DB_PATH):
        print("data/laws.db 가 없습니다. 먼저 importer.py를 실행하세요.")
        input("아무 키나 누르면 종료합니다...")
        sys.exit(1)
    url = f"http://localhost:{PORT}"
    try:
        srv = ThreadingHTTPServer((host, PORT), Handler)
    except OSError:
        print(f"포트 {PORT} 가 이미 사용 중입니다 — 서버가 이미 켜져 있는 것 같습니다.")
        print(f"브라우저에서 {url} 를 엽니다.")
        webbrowser.open(url)
        input("아무 키나 누르면 이 창을 닫습니다...")
        sys.exit(0)
    print(f"안전법규 DB 서버 실행 중: {url}")
    print("이 창을 닫으면 서버가 종료됩니다. (종료: Ctrl+C)")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("서버를 종료합니다.")


if __name__ == "__main__":
    main()
