# -*- coding: utf-8 -*-
"""verify_db.py — DB 품질 전면 감사.

"조용한 실패"를 잡기 위한 도구. importer 실행 후 반드시 돌린다.
(실제 사례: HWP의 NULL 문자로 별표 본문이 18자로 잘렸는데 건수는 정상이라
 몇 주 동안 아무도 몰랐다. 이런 실패는 건수가 아니라 내용을 재봐야 잡힌다.)

검사 항목
  [1] 구성 요약        — 종류별 건수가 기대 범위인가
  [2] 본문 무결성      — NULL/제어문자 잔존, 비정상적으로 짧은 본문
  [3] 별표 손실률      — 원본 파일에서 재추출한 길이 vs DB 저장 길이
  [4] 물질 스팟체크    — 주요 취급 화학물질 20종의 규제 매핑 존재 여부
  [5] 검색 스팟체크    — 대표 질의가 기대 문서를 상위에서 찾는가

실행: python verify_db.py          (요약만)
      python verify_db.py --full   (별표 손실률 전수 — 원본 재파싱, 수 분)
종료코드: 문제 발견 시 1 (자동화 파이프라인에서 활용)
"""
import sys, io, os, re, json, sqlite3

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "data", "laws.db")
BUNDLE = os.path.join(BASE, "bundle")

PROBLEMS = []


def problem(msg):
    PROBLEMS.append(msg)
    print(f"    [!] {msg}")


def ok(msg):
    print(f"    [v] {msg}")


# ── [1] 구성 요약 ─────────────────────────────────────────────
def check_composition(con):
    print("\n[1] 구성 요약")
    exp = {"law": (4000, 5000), "admrul": (400, 800), "kosha": (4000, 7000)}
    for kind, n in con.execute(
            "SELECT l.kind, COUNT(*) FROM articles a"
            " JOIN laws l ON l.law_id=a.law_id GROUP BY l.kind"):
        lo, hi = exp.get(kind, (1, 10**9))
        line = f"{kind}: {n:,}건"
        if lo <= n <= hi:
            ok(line)
        else:
            problem(f"{line} — 기대범위({lo:,}~{hi:,}) 벗어남")
    annex = con.execute(
        "SELECT COUNT(*), SUM(LENGTH(content)) FROM articles WHERE chapter='별표'"
    ).fetchone()
    ok(f"별표 청크 {annex[0]:,}건 / {annex[1]:,}자")
    subs = con.execute("SELECT COUNT(DISTINCT cas) FROM substances WHERE cas!=''").fetchone()[0]
    if subs < 2000:
        problem(f"물질 고유 CAS {subs:,}종 — 2,000종 미만(ICIS+별표 통합 후 기준 미달)")
    else:
        ok(f"물질 고유 CAS {subs:,}종")


# ── [2] 본문 무결성 ───────────────────────────────────────────
def check_integrity(con):
    print("\n[2] 본문 무결성")
    # SQLite char(0)은 신뢰할 수 없어 파이썬으로 직접 스캔
    n_null = n_ctrl = 0
    for (c,) in con.execute("SELECT content FROM articles"):
        if "\x00" in c:
            n_null += 1
        elif re.search(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", c):
            n_ctrl += 1
    if n_null:
        problem(f"NULL 문자 포함 본문 {n_null}건 — clean_text 미적용 경로 존재")
    else:
        ok("NULL 문자 0건")
    if n_ctrl:
        problem(f"기타 제어문자 포함 본문 {n_ctrl}건")
    else:
        ok("제어문자 0건")

    short = con.execute("""SELECT COUNT(*) FROM articles a
                           WHERE a.chapter='별표' AND LENGTH(a.content) < 300
                             AND a.title NOT LIKE '%삭제%' AND a.jo_branch=0""").fetchone()[0]
    print(f"    (정보) 300자 미만 별표 {short}건 — 손실 여부는 [3]에서 원본 대조로 판정")


# ── [3] 별표 손실률 (--full) ─────────────────────────────────
def check_annex_loss(con, full=False):
    print("\n[3] 별표 손실률" + ("" if full else " (요약 — 전수는 --full)"))
    mpath = os.path.join(BUNDLE, "annex_manifest.json")
    if not os.path.exists(mpath):
        problem("annex_manifest.json 없음")
        return
    sys.path.insert(0, BASE)
    from importer import extract_annex_text
    annexes = json.load(open(mpath, encoding="utf-8"))["annexes"]
    if full:
        targets = annexes
    else:
        # 기본 모드: 큰 파일 + DB에 300자 미만으로 들어간 별표(손실 의심)만
        shorts = {(r[0], r[1]) for r in con.execute(
            """SELECT l.name, a.jo_short FROM articles a
               JOIN laws l ON l.law_id=a.law_id
               WHERE a.chapter='별표' AND LENGTH(a.content)<300 AND a.jo_branch=0""")}
        targets = [a for a in annexes
                   if os.path.getsize(os.path.join(BUNDLE, a["file"])) > 50000
                   or (a["law_name"], a["label"]) in shorts]
    checked = lossy = 0
    for a in targets:
        p = os.path.join(BUNDLE, a["file"])
        if not os.path.exists(p):
            continue
        src = extract_annex_text(p)
        if len(src) < 500:
            continue
        db_len = con.execute(
            """SELECT SUM(LENGTH(content)) FROM articles a2
               JOIN laws l ON l.law_id=a2.law_id
               WHERE l.name=? AND a2.jo_short=? AND a2.chapter='별표'""",
            (a["law_name"], a["label"])).fetchone()[0] or 0
        checked += 1
        if db_len < len(src) * 0.9:
            lossy += 1
            problem(f"손실: [{a['law_name'][:24]}] {a['label']} "
                    f"원본 {len(src):,}자 → DB {db_len:,}자")
    if lossy == 0:
        ok(f"검사한 {checked}건 모두 원본 대비 90% 이상 보존")


# ── [4] 물질 스팟체크 ─────────────────────────────────────────
SPOT_CHEMICALS = [
    ("7664-39-3", "불화수소", True), ("7664-93-9", "황산", True),
    ("7647-01-0", "염산", True), ("7697-37-2", "질산", True),
    ("108-88-3", "톨루엔", True), ("67-56-1", "메탄올", True),
    ("7664-41-7", "암모니아", True), ("75-21-8", "산화에틸렌", True),
    ("7782-50-5", "염소", True), ("124-40-3", "디메틸아민", True),
    ("110-54-3", "노말헥산", False), ("71-43-2", "벤젠", True),
    ("50-00-0", "포름알데히드", True), ("7783-06-4", "황화수소", True),
    ("74-90-8", "시안화수소", True), ("100-42-5", "스티렌", False),
    ("127-18-4", "퍼클로로에틸렌", True), ("75-09-2", "디클로로메탄", True),
    ("64-19-7", "아세트산", False), ("67-63-0", "이소프로필알코올", False),
]


def check_substances(con):
    print("\n[4] 물질 스팟체크 (주요 취급물질 20종)")
    miss_any, miss_haz = [], []
    for cas, name, expect_haz in SPOT_CHEMICALS:
        total = con.execute("SELECT COUNT(*) FROM substances WHERE cas=?", (cas,)).fetchone()[0]
        haz = con.execute(
            "SELECT COUNT(*) FROM substances WHERE cas=? AND"
            " (annex LIKE '%규정수량%' OR annex LIKE '%지정 목록%')", (cas,)).fetchone()[0]
        if total == 0:
            miss_any.append(name)
        elif expect_haz and haz == 0:
            miss_haz.append(name)
    if miss_any:
        problem(f"등재 0건 물질: {miss_any}")
    if miss_haz:
        problem(f"유해화학물질 지정 누락 의심: {miss_haz}")
    if not miss_any and not miss_haz:
        ok("20종 전부 등재, 유해화학물질 지정 예상과 일치")


# ── [5] 검색 스팟체크 ─────────────────────────────────────────
SPOT_QUERIES = [
    ("유해화학물질 영업허가", "화학물질관리법", 5),
    ("화학사고예방관리계획서", "화학물질관리법", 5),
    ("물질안전보건자료 작성", "산업안전보건법", 5),
    ("독성가스 저장", "고압가스", 10),
    ("지정폐기물 처리", "폐기물관리법", 5),
    ("톨루엔 노출기준", "노출기준", 5),
    ("디메틸아민 규정수량", "규정수량", 5),
]


def check_search(con):
    print("\n[5] 검색 스팟체크 (기대 법령이 상위 N위 안에)")
    for q, expect_law, topn in SPOT_QUERIES:
        terms = [t for t in q.split() if len(t) >= 3]
        match = " AND ".join(f'"{t}"' for t in terms)
        try:
            rows = con.execute(
                """SELECT l.name FROM articles_fts f
                   JOIN articles a ON a.article_id=f.rowid
                   JOIN laws l ON l.law_id=a.law_id
                   WHERE articles_fts MATCH ?
                   ORDER BY bm25(articles_fts, 8.0, 4.0, 1.0)
                    + (CASE WHEN l.kind='kosha' THEN 2.5 ELSE 0 END) LIMIT ?""",
                (match, topn)).fetchall()
        except sqlite3.OperationalError as e:
            problem(f"'{q}' FTS 오류: {e}")
            continue
        names = [r[0] for r in rows]
        if any(expect_law in n for n in names):
            ok(f"'{q}' → {expect_law} 상위 {topn}위 내")
        else:
            problem(f"'{q}' → 기대({expect_law}) 없음. 상위: {[n[:16] for n in names[:3]]}")


def main():
    full = "--full" in sys.argv
    if not os.path.exists(DB):
        print("data/laws.db 없음")
        sys.exit(1)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    print("=" * 60)
    print(" DB 품질 감사")
    print("=" * 60)
    check_composition(con)
    check_integrity(con)
    check_annex_loss(con, full)
    check_substances(con)
    check_search(con)
    print("\n" + "=" * 60)
    if PROBLEMS:
        print(f" 결과: 문제 {len(PROBLEMS)}건 — 위 [!] 항목 확인 필요")
        sys.exit(1)
    print(" 결과: 전 항목 통과")


if __name__ == "__main__":
    main()
