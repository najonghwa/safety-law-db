# -*- coding: utf-8 -*-
"""search_law.py — 안전법규 DB 검색 도구 (Claude Code가 호출).

표준 라이브러리만 사용(sqlite3) — 별도 설치 불필요.
data/laws.db 에서 조문·별표·물질 규제를 검색한다.

사용법 (Claude가 실행):
  python search_law.py find "유해화학물질 영업허가"     # 조문 검색
  python search_law.py find "독성가스" --tier 법률       # 등급 필터(법률/시행령/시행규칙/고시/KOSHA)
  python search_law.py article 1234                       # 조문 전문 보기(article_id)
  python search_law.py substance 7664-39-3               # 물질(CAS) 규제 등재 조회
  python search_law.py laws                               # 수록 법령 목록
  python search_law.py laws 화학물질관리법                # 특정 법령의 조문 목록

출력은 JSON. Claude는 이 결과의 조문만 근거로 인용하고,
검색 결과가 없으면 추측하지 말고 "확인 불가"로 답한다.
"""
import sys, io, os, re, json, sqlite3

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "data", "laws.db")
SYN = os.path.join(BASE, "synonyms.json")


def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def db_info():
    """DB 수집 기준일과 경과일. 오래되면 경고 문구를 함께 반환."""
    try:
        con = db()
        row = con.execute("SELECT value FROM meta WHERE key='bundle_created_at'").fetchone()
        con.close()
        if not row:
            return {}
        stamp = row[0][:10]
        info = {"db_기준일": stamp}
        try:
            import datetime
            d = datetime.date.fromisoformat(stamp)
            days = (datetime.date.today() - d).days
            info["경과일"] = days
            if days > 90:
                info["경고"] = (f"이 DB는 {stamp} 기준으로 {days}일 지났습니다. "
                                "그 사이 개정된 법령은 반영되어 있지 않으므로, "
                                "답변에 이 사실을 밝히고 최신 DB 반입 또는 "
                                "국가법령정보센터 확인을 안내하세요.")
        except Exception:
            pass
        return info
    except Exception:
        return {}


def out(obj):
    if isinstance(obj, dict):
        obj = {**obj, **db_info()}
    print(json.dumps(obj, ensure_ascii=False, indent=1))


def load_synonyms():
    try:
        with open(SYN, encoding="utf-8") as f:
            return json.load(f)["synonyms"]
    except Exception:
        return {}


def expand(q):
    """현장 용어 → 법령 용어 변형 쿼리 목록."""
    variants, notes = [q], []
    for term, subs in load_synonyms().items():
        if term in q:
            notes.append({"term": term, "to": subs})
            for s in subs:
                v = q.replace(term, s)
                if v not in variants:
                    variants.append(v)
    return variants[:8], notes


def _search_one(con, q, tier, limit):
    terms = [t for t in re.split(r"\s+", q) if t]
    fts_terms = [t for t in terms if len(t) >= 3]
    short = [t for t in terms if 0 < len(t) < 3]
    tier_sql = " AND l.tier=?" if tier else ""
    rows = []
    if fts_terms:
        match = " AND ".join('"' + t.replace('"', "") + '"' for t in fts_terms)
        short_sql = " AND a.content LIKE ?" * len(short)
        # 랭킹: 제목·별표명 가중 + KOSHA 지침은 법령·고시보다 소폭 뒤로
        # (같은 관련도면 법적 근거가 먼저 나와야 실무 인용에 맞음)
        sql = ("SELECT a.article_id, a.jo_label, a.jo_short, a.title, a.chapter,"
               " l.name law_name, l.tier,"
               " snippet(articles_fts, 2, '<<', '>>', ' … ', 26) snip"
               " FROM articles_fts f JOIN articles a ON a.article_id=f.rowid"
               " JOIN laws l ON l.law_id=a.law_id"
               " WHERE articles_fts MATCH ?" + short_sql + tier_sql +
               " ORDER BY bm25(articles_fts, 8.0, 4.0, 1.0)"
               "  + (CASE WHEN l.kind='kosha' THEN 2.5 ELSE 0 END) LIMIT ?")
        params = [match] + [f"%{t}%" for t in short] + ([tier] if tier else []) + [limit]
        try:
            rows = [dict(r) for r in con.execute(sql, params).fetchall()]
        except sqlite3.OperationalError:
            rows = []
    if not rows:  # 짧은 검색어 폴백
        sql = ("SELECT a.article_id, a.jo_label, a.jo_short, a.title, a.chapter,"
               " l.name law_name, l.tier,"
               " substr(a.content, 1, 160) snip"
               " FROM articles a JOIN laws l ON l.law_id=a.law_id"
               " WHERE a.content LIKE ?" + tier_sql + " LIMIT ?")
        params = [f"%{q}%"] + ([tier] if tier else []) + [limit]
        rows = [dict(r) for r in con.execute(sql, params).fetchall()]
    return rows


def cmd_find(q, tier="", limit=15):
    con = db()
    variants, notes = expand(q)
    seen, results = set(), []
    for v in variants:
        for r in _search_one(con, v, tier, limit):
            if r["article_id"] in seen:
                continue
            seen.add(r["article_id"])
            r["snip"] = re.sub(r"\s+", " ", r["snip"]).strip()
            results.append(r)
    con.close()
    out({"query": q, "synonym_expansion": notes,
         "count": len(results), "results": results[:limit],
         "hint": "인용할 조문의 전문이 필요하면: python search_law.py article <article_id>"})


def cmd_article(aid):
    con = db()
    r = con.execute(
        "SELECT a.article_id, a.jo_label, a.jo_short, a.title, a.chapter, a.content,"
        " l.name law_name, l.tier, l.enforce, l.source_url"
        " FROM articles a JOIN laws l ON l.law_id=a.law_id WHERE a.article_id=?",
        (aid,)).fetchone()
    con.close()
    if not r:
        out({"error": "해당 article_id 없음", "article_id": aid})
        return
    out(dict(r))


def cmd_substance(q):
    con = db()
    if re.fullmatch(r"\d{2,7}-\d{2}-\d", q):
        rows = con.execute(
            "SELECT cas, name, law_name, annex, context FROM substances WHERE cas=?",
            (q,)).fetchall()
    else:
        rows = con.execute(
            "SELECT cas, name, law_name, annex, context FROM substances"
            " WHERE name LIKE ? OR context LIKE ? LIMIT 100",
            (f"%{q}%", f"%{q}%")).fetchall()
    seen, matches = set(), []
    for r in rows:
        key = (r["law_name"], r["annex"], r["cas"])
        if key in seen:
            continue
        seen.add(key)
        d = dict(r)
        label = (r["annex"] or "").split(" ")[0]
        art = con.execute(
            "SELECT a.article_id FROM articles a JOIN laws l ON l.law_id=a.law_id"
            " WHERE l.name=? AND a.jo_short=? ORDER BY a.jo_branch LIMIT 1",
            (r["law_name"], label)).fetchone()
        d["annex_article_id"] = art["article_id"] if art else None
        matches.append(d)
    con.close()
    out({"query": q, "count": len(matches), "listings": matches,
         "note": "이 목록은 별표 원문 대조 결과(확정). 별표 전문은 annex_article_id로 article 조회.",
         "판정_주의": (
             "유해화학물질 지정은 「유해화학물질의 규정수량에 관한 규정」 별표2(고유번호·규정수량 포함)를 "
             "우선 근거로 삼으세요. ICIS 분류 목록은 보조 자료이며 누락이 있을 수 있습니다. "
             "또한 목록에 없다고 규제 대상이 아니라고 단정하지 마세요 — 수용액·혼합물은 함량 기준으로 "
             "별도 규제될 수 있고, 고압가스법·위험물법·산안법 규제는 별개입니다.")})


def cmd_laws(name=""):
    con = db()
    if name:
        law = con.execute("SELECT law_id, name, enforce, source_url FROM laws"
                          " WHERE name=?", (name,)).fetchone()
        if not law:
            like = con.execute("SELECT name FROM laws WHERE name LIKE ? LIMIT 10",
                               (f"%{name}%",)).fetchall()
            con.close()
            out({"error": "정확한 법령명 아님", "did_you_mean": [r["name"] for r in like]})
            return
        arts = con.execute(
            "SELECT article_id, jo_label, title, chapter FROM articles"
            " WHERE law_id=? ORDER BY jo_no, jo_branch", (law["law_id"],)).fetchall()
        con.close()
        out({"law": dict(law), "article_count": len(arts),
             "articles": [dict(a) for a in arts]})
    else:
        rows = con.execute(
            "SELECT grp, name, tier, enforce, article_count FROM laws ORDER BY law_id"
        ).fetchall()
        con.close()
        groups = {}
        for r in rows:
            groups.setdefault(r["grp"], []).append(dict(r))
        out({"group_count": len(groups),
             "groups": [{"group": g, "laws": v} for g, v in groups.items()]})


USAGE = """안전법규 DB 검색 도구
  python search_law.py find "<검색어>" [--tier 법률|시행령|시행규칙|고시|KOSHA]
  python search_law.py article <article_id>
  python search_law.py substance <CAS번호 또는 물질명>
  python search_law.py laws [법령명]"""


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(USAGE)
        return
    cmd = a[0]
    if cmd == "find" and len(a) >= 2:
        tier = ""
        if "--tier" in a:
            i = a.index("--tier")
            tier = a[i + 1] if i + 1 < len(a) else ""
            a = a[:i] + a[i + 2:]
        cmd_find(a[1], tier)
    elif cmd == "article" and len(a) >= 2:
        cmd_article(int(a[1]))
    elif cmd == "substance" and len(a) >= 2:
        cmd_substance(a[1])
    elif cmd == "laws":
        cmd_laws(a[1] if len(a) >= 2 else "")
    else:
        print(USAGE)


if __name__ == "__main__":
    main()
