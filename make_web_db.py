# -*- coding: utf-8 -*-
"""make_web_db.py — 웹 배포용(Vercel) 경량 DB 생성.

로컬 data/laws.db는 100MB다. 대부분이 FTS 트라이그램 인덱스라 서버리스에
그대로 올리기엔 무겁다. KOSHA 지침(법령이 아니라 참고 지침, 조문 5,027개)을
빼면 37MB가 되는데, 평가셋 검색 정확도는 100% 그대로였다. 그래서 웹에는 이 판을 쓴다.

  1) laws.db 복사 → 2) KOSHA 행 삭제 → 3) FTS 재구축 → 4) VACUUM → 5) 검증

FTS가 외부 콘텐츠 테이블(content='articles')이라, 행만 지우면 인덱스에는
삭제된 조문이 남아 검색에 유령 결과가 뜬다. 반드시 rebuild 해야 한다.

실행:
  python make_web_db.py              # 웹 저장소 위치를 찾아 바로 덮어씀
  python make_web_db.py 출력.db      # 경로 지정
"""
import os, sys, io, shutil, sqlite3

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
BASE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(BASE, "data", "laws.db")
# Desktop/Claude/schedule/safety_law → Desktop/safety_law_web
WEB = os.path.abspath(os.path.join(BASE, "..", "..", "..", "safety_law_web", "data", "laws.db"))

# 검증용 — 이 검색어들이 결과 0건이면 인덱스가 깨진 것이다
PROBES = ["유해화학물질 영업허가", "물질안전보건자료", "지정폐기물", "고압가스 저장소",
          "화학사고예방관리계획서", "위험물 저장소", "작업환경측정"]


def mb(path):
    return os.path.getsize(path) / 1024 / 1024


def main():
    if not os.path.exists(SRC):
        sys.exit(f"원본이 없습니다: {SRC}\n먼저 importer.py를 실행하세요.")
    out = sys.argv[1] if len(sys.argv) > 1 else WEB
    outdir = os.path.dirname(out)
    if not os.path.isdir(outdir):
        sys.exit(f"출력 폴더가 없습니다: {outdir}\n경로를 인자로 넘기세요: python make_web_db.py 출력.db")

    print(f"원본 : {SRC}  ({mb(SRC):.0f}MB)")
    print(f"출력 : {out}")

    tmp = out + ".tmp"
    shutil.copy2(SRC, tmp)
    con = sqlite3.connect(tmp)
    con.row_factory = sqlite3.Row

    before = con.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    stamp = con.execute("SELECT value FROM meta WHERE key='bundle_created_at'").fetchone()
    n_kosha = con.execute("SELECT COUNT(*) FROM laws WHERE kind='kosha'").fetchone()[0]

    con.execute("DELETE FROM articles WHERE law_id IN (SELECT law_id FROM laws WHERE kind='kosha')")
    con.execute("DELETE FROM laws WHERE kind='kosha'")
    # 외부 콘텐츠 FTS — 본문 행을 지웠으니 인덱스를 다시 만들어야 한다
    con.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
    con.commit()
    con.execute("VACUUM")
    con.commit()

    after = con.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    laws = con.execute("SELECT COUNT(*) FROM laws").fetchone()[0]
    subs = con.execute("SELECT COUNT(DISTINCT cas) FROM substances WHERE cas!=''").fetchone()[0]

    print(f"\nKOSHA 지침 {n_kosha}건 제외 → 조문 {before:,} → {after:,}")
    print(f"법령 {laws} · 물질(CAS) {subs:,} · 수집일 {(stamp[0] if stamp else '?')[:10]}")

    # ── 검증: 인덱스가 실제로 동작하는지, 유령 결과가 없는지 ──
    print("\n[검증]")
    bad = 0
    for p in PROBES:
        terms = " AND ".join(f'"{t}"' for t in p.split())
        rows = con.execute(
            "SELECT a.article_id FROM articles_fts f JOIN articles a ON a.article_id=f.rowid"
            " WHERE articles_fts MATCH ? LIMIT 5", (terms,)).fetchall()
        # rowid가 articles에 없으면 인덱스에 유령이 남은 것 (JOIN에서 걸러지므로 개수로 비교)
        raw = con.execute("SELECT COUNT(*) FROM articles_fts WHERE articles_fts MATCH ?",
                          (terms,)).fetchone()[0]
        joined = con.execute(
            "SELECT COUNT(*) FROM articles_fts f JOIN articles a ON a.article_id=f.rowid"
            " WHERE articles_fts MATCH ?", (terms,)).fetchone()[0]
        ghost = raw - joined
        ok = bool(rows) and ghost == 0
        bad += not ok
        print(f"  {'O' if ok else 'X'} {p:24s} {joined:4d}건" +
              (f"  ← 유령 {ghost}건" if ghost else ""))

    con.close()
    if bad:
        os.remove(tmp)
        sys.exit(f"\n검증 실패 {bad}건 — 출력하지 않았습니다.")

    if os.path.exists(out):
        os.remove(out)
    os.rename(tmp, out)
    print(f"\n완료: {out}  ({mb(out):.0f}MB)")
    print(f"다음 — 반드시 이 폴더로 옮긴 뒤 git을 치세요: {os.path.dirname(outdir)}")


if __name__ == "__main__":
    main()
