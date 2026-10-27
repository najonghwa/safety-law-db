# -*- coding: utf-8 -*-
"""깃허브에서 받은 DB 압축(data/laws.db.gz)을 풀고 검색 인덱스를 만든다 — 서버에서 처음 한 번, git pull 할 때마다.

  git clone https://github.com/najonghwa/safety-law-db.git
  cd safety-law-db/dist/she-law-db
  python3 setup_db.py
  python3 she_law_mcp.py --http 0.0.0.0:8780

DB 파일(150MB)은 깃허브 한도(파일당 100MB)를 넘어서, 검색 인덱스를 뺀 DB를 gzip으로 묶어 올린다(약 12MB).
압축이 지난번과 같으면 아무것도 하지 않는다(--force로 다시 만들기).
"""
import os, sys, io, gzip, shutil, sqlite3, hashlib

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
BASE = os.path.dirname(os.path.abspath(__file__))
GZ = os.path.join(BASE, "data", "laws.db.gz")
DB = os.path.join(BASE, "data", "laws.db")
STAMP = os.path.join(BASE, "data", ".laws.db.gz.sha256")   # 마지막으로 푼 압축의 지문


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_index(path):
    """메일 패키지 복원과 같은 전문검색 인덱스(trigram)."""
    con = sqlite3.connect(path)
    con.execute("DROP TABLE IF EXISTS articles_fts")
    con.execute("CREATE VIRTUAL TABLE articles_fts USING fts5("
                "jo_label, title, content, tokenize='trigram', "
                "content='articles', content_rowid='article_id')")
    con.execute("INSERT INTO articles_fts(rowid, jo_label, title, content) "
                "SELECT article_id, jo_label, title, content FROM articles")
    con.commit()
    con.close()


def check(path):
    """건수·검색·본문을 확인한다. 받다 끊긴 압축은 gzip 단계에서 이미 걸러진다."""
    con = sqlite3.connect(path)
    g = lambda q: con.execute(q).fetchone()[0]
    laws, arts = g("SELECT count(*) FROM laws"), g("SELECT count(*) FROM articles")
    subs = g("SELECT count(DISTINCT cas) FROM substances")
    hit = g("SELECT count(*) FROM articles_fts WHERE articles_fts MATCH '\"영업허가\"'")
    body = g("SELECT SUM(LENGTH(content)) FROM articles") or 0
    stamp = (con.execute("SELECT value FROM meta WHERE key='bundle_created_at'").fetchone() or ["?"])[0][:10]
    con.close()
    print(f"  DB 기준일 {stamp} · 법령·지침 {laws}건 · 검색 단위 {arts:,}건 · 물질 {subs:,}종 · '영업허가' 검색 {hit}건")
    return laws > 50 and arts > 9000 and hit > 0 and body > 10_000_000


def main():
    if not os.path.exists(GZ):
        print("!! data/laws.db.gz 가 없습니다 — git pull 이 끝났는지 확인하세요.")
        sys.exit(1)
    fp = sha256(GZ)
    if (os.path.exists(DB) and os.path.exists(STAMP) and "--force" not in sys.argv
            and open(STAMP, encoding="ascii").read().strip() == fp):
        print("[OK] 이미 최신 DB입니다.")
        check(DB)
        return
    tmp = DB + ".new"
    print("[*] 압축 푸는 중...")
    with gzip.open(GZ, "rb") as fi, open(tmp, "wb") as fo:
        shutil.copyfileobj(fi, fo)
    print("[*] 검색 인덱스 만드는 중... (1~2분)")
    build_index(tmp)
    if not check(tmp):
        os.remove(tmp)
        print("!! 값이 예상과 다릅니다 — git pull 을 다시 하고 재실행하세요.")
        sys.exit(1)
    # 켜져 있는 서버는 질문마다 DB를 새로 열므로, 파일만 바꿔 끼우면 다음 질문부터 새 DB를 쓴다
    os.replace(tmp, DB)
    with open(STAMP, "w", encoding="ascii") as f:
        f.write(fp)
    print("[OK] DB 준비 완료. 서버:  python3 she_law_mcp.py --http 0.0.0.0:8780")


if __name__ == "__main__":
    main()
