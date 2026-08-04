# -*- coding: utf-8 -*-
"""make_mail_package.py — 메일 전송용 txt 패키지 생성 (인터넷 되는 PC에서 실행).

법령 개정 후 DB를 갱신했을 때 이 스크립트만 돌리면
dist/mail/ 폴더에 메일 첨부용 txt가 새로 만들어진다.

전체 갱신 절차:
    python collector.py          # 법령·고시 (개정분만)
    python annex_collector.py    # 별표
    python kosha_collector.py    # KOSHA 지침
    python importer.py           # data/laws.db 재생성
    python make_mail_package.py  # ← 이 파일: 메일용 txt 생성

사내에서는 받은 txt로 restore.py 실행 → data/laws.db만 교체하면 끝.
"""
import os, io, re, sys, gzip, base64, sqlite3, shutil

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
BASE = os.path.dirname(os.path.abspath(__file__))
SRC_DB = os.path.join(BASE, "data", "laws.db")
PKG = os.path.join(BASE, "dist", "she-law-db")
OUT = os.path.join(BASE, "dist", "mail")
CHUNK = 5_000_000          # 조각당 약 4.8MB (메일 첨부 한도 고려)
LINE = 200                 # base64 줄바꿈 폭


def build_db_parts():
    """FTS 인덱스를 뺀 DB → gzip → base64 → 분할 txt (인덱스는 사내에서 재생성)."""
    tmp = os.path.join(OUT, "_tmp_slim.db")
    shutil.copy(SRC_DB, tmp)
    con = sqlite3.connect(tmp)
    con.execute("DROP TABLE IF EXISTS articles_fts")
    con.commit()
    con.execute("VACUUM")
    stamp = con.execute("SELECT value FROM meta WHERE key='bundle_created_at'").fetchone()
    con.close()

    raw = open(tmp, "rb").read()
    os.remove(tmp)
    b64 = base64.b64encode(gzip.compress(raw, 9)).decode("ascii")

    # 이전 조각 삭제(개수가 달라질 수 있으므로)
    for f in os.listdir(OUT):
        if f.startswith("DB_part"):
            os.remove(os.path.join(OUT, f))

    parts = [b64[i:i + CHUNK] for i in range(0, len(b64), CHUNK)]
    total = 0
    for i, p in enumerate(parts, 1):
        name = f"DB_part{i}_of_{len(parts)}.txt"
        with open(os.path.join(OUT, name), "w", encoding="ascii", newline="\n") as f:
            for j in range(0, len(p), LINE):
                f.write(p[j:j + LINE] + "\n")
        size = os.path.getsize(os.path.join(OUT, name))
        total += size
        print(f"  {name}: {size/1024/1024:.1f}MB")
    date = stamp[0][:10] if stamp else "?"
    print(f"DB: 원본 {len(raw)/1024/1024:.0f}MB → 전송 {total/1024/1024:.1f}MB "
          f"({len(parts)}조각) / 기준일 {date}")
    return date


DESC = {
    "00_먼저읽기_복원안내.txt": "이 파일",
    "RESTORE_py.txt": "복원 스크립트",
    "CODE_search_law_py.txt": "검색 도구",
    "DOC_CLAUDE_md.txt": "답변 규칙",
    "DOC_SKILL_md.txt": "검색 매뉴얼",
    "DOC_README_md.txt": "사용 안내",
    "DATA_synonyms_json.txt": "용어 사전",
}


def stamp_guide(date):
    """안내문의 [DB 기준일]과 첨부파일 목록을 실제 생성 결과로 갱신."""
    guide = os.path.join(OUT, "00_먼저읽기_복원안내.txt")
    if not os.path.exists(guide):
        print("  ! 안내문이 없습니다")
        return
    txt = open(guide, encoding="utf-8").read()
    txt = re.sub(r"\[DB 기준일:[^\]]*\]", f"[DB 기준일: {date} ]", txt, count=1)

    # 실제 파일 목록으로 첨부 목록 블록 재생성
    files = sorted(f for f in os.listdir(OUT) if f.endswith(".txt"))
    parts = sorted(f for f in files if f.startswith("DB_part"))
    lines = []
    for f in files:
        size = os.path.getsize(os.path.join(OUT, f))
        if f.startswith("DB_part"):
            m = re.match(r"DB_part(\d+)_of_(\d+)", f)
            desc = f"법령 DB 조각 {m.group(1)}/{m.group(2)}"
            lines.append(f"  {f:<30}{desc:<20}({size/1024/1024:.1f}MB)")
        else:
            lines.append(f"  {f:<30}{DESC.get(f, '')}")
    block = (f"[ 첨부파일 — {len(files)}개가 다 있어야 합니다 ]\n"
             + "-" * 68 + "\n" + "\n".join(lines) + "\n")
    new = re.sub(r"\[ 첨부파일 —[^\]]*\]\n-+\n(?:.*\n)*?\n",
                 block + "\n", txt, count=1)
    if new == txt:
        print("  ! 안내문 첨부 목록 자리를 찾지 못했습니다 — 수동 확인 필요")
    else:
        txt = new
    open(guide, "w", encoding="utf-8").write(txt)
    print(f"  안내문 갱신: 기준일 {date}, 첨부 {len(files)}개(DB {len(parts)}조각)")


def copy_docs():
    """코드·문서를 .txt 확장자로 복사 (메일 확장자 차단 회피)."""
    pairs = [
        (os.path.join(PKG, "search_law.py"), "CODE_search_law_py.txt"),
        (os.path.join(PKG, "CLAUDE.md"), "DOC_CLAUDE_md.txt"),
        (os.path.join(PKG, ".claude", "skills", "she-law", "SKILL.md"), "DOC_SKILL_md.txt"),
        (os.path.join(PKG, "README.md"), "DOC_README_md.txt"),
        (os.path.join(PKG, "synonyms.json"), "DATA_synonyms_json.txt"),
    ]
    for src, dst in pairs:
        if os.path.exists(src):
            shutil.copy(src, os.path.join(OUT, dst))
            print(f"  {dst}")
        else:
            print(f"  ! {src} 없음")


def main():
    if not os.path.exists(SRC_DB):
        print("data/laws.db 가 없습니다. importer.py를 먼저 실행하세요.")
        sys.exit(1)
    os.makedirs(OUT, exist_ok=True)

    # 배포 패키지의 DB도 최신으로 맞춤
    shutil.copy(SRC_DB, os.path.join(PKG, "data", "laws.db"))

    print("[1/2] DB 인코딩·분할")
    date = build_db_parts()
    print("[2/2] 코드·문서 복사")
    copy_docs()
    stamp_guide(date)

    files = sorted(f for f in os.listdir(OUT) if f.endswith(".txt"))
    total = sum(os.path.getsize(os.path.join(OUT, f)) for f in files)
    print(f"\n완료: {len(files)}개 파일, 합계 {total/1024/1024:.1f}MB")
    print(f"위치: {OUT}")
    print("→ 이 폴더의 txt 전부를 메일에 첨부해 사내로 보내세요.")
    missing = [f for f in ("00_먼저읽기_복원안내.txt", "RESTORE_py.txt") if f not in files]
    if missing:
        print(f"!! 안내문/복원스크립트 누락: {missing} — dist/mail 에 있어야 합니다")


if __name__ == "__main__":
    main()
