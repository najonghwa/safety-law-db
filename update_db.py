# -*- coding: utf-8 -*-
"""update_db.py — 법령 DB 갱신 전 과정을 순서대로 실행한다.

수집 → 적재 → 감사 → 웹 배포용 DB 생성까지 한 번에. 인터넷이 필요하다.
중간에 하나라도 실패하면 거기서 멈춘다(깨진 DB가 배포로 넘어가지 않게).

배치 파일로 안내 문구를 쓰면 cmd가 한글 UTF-8을 파싱하다 깨지므로,
DB갱신.bat은 이 파일을 부르기만 하고 안내는 전부 여기서 한다.

실행: python update_db.py
      python update_db.py --local   # 웹 배포용 DB 생성은 건너뛴다
"""
import os, sys, io, time, subprocess

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace", line_buffering=True)
BASE = os.path.dirname(os.path.abspath(__file__))

STEPS = [
    ("collector.py", "법령 본문 수집 (바뀐 것만 받습니다)"),
    ("annex_collector.py", "별표 수집"),
    ("substance_collector.py", "화학물질 규제 목록 수집"),
    ("kosha_collector.py", "KOSHA 지침 목록·PDF 수집"),
    ("ocr_scans.py", "스캔본·그림 별표 OCR (winocr 없으면 건너뜀)"),
    ("importer.py", "DB 재생성"),
    ("verify_db.py", "DB 감사"),
]
BAR = "=" * 60


def run(script, label, i, n):
    print(f"\n{BAR}\n [{i}/{n}] {label}\n{BAR}")
    t0 = time.time()
    r = subprocess.run([sys.executable, script], cwd=BASE)
    if r.returncode != 0:
        print(f"\n{'#' * 60}")
        print(f" 실패: {script} (종료코드 {r.returncode})")
        print(" 위 오류 메시지를 확인하세요.")
        print(" 인터넷이 끊겼거나 law.go.kr 화면 구조가 바뀐 경우입니다.")
        print(" 수집기가 '!! … 이전 판 유지'를 냈다면 시간 초과입니다 — 그냥 다시 실행하면")
        print(" 그 항목만 재시도됩니다. (빠진 채로 다음 단계로 넘어가지 않게 여기서 멈춘 것)")
        print(f"{'#' * 60}")
        sys.exit(r.returncode)
    print(f"  ({time.time() - t0:.0f}초)")


def main():
    local_only = "--local" in sys.argv
    print(f"{BAR}\n 법령 DB 갱신 — 인터넷 연결이 필요합니다.\n"
          f" 전체 5~15분 걸립니다. 창을 닫지 마세요.\n{BAR}")

    for i, (script, label) in enumerate(STEPS, 1):
        run(script, label, i, len(STEPS))

    print(f"\n{BAR}\n 로컬 DB 갱신 완료.\n{BAR}")
    web = os.path.abspath(os.path.join(BASE, "..", "..", "..", "safety_law_web"))
    if local_only:
        return
    if not os.path.isdir(web):
        # 웹 배포는 이 저장소를 쓰는 모두에게 해당되는 일이 아니다. 폴더가 없으면
        # 실패가 아니라 '해당 없음'이다 — 여기서 종료코드 1을 내면 갱신이 실패한 것처럼 보인다.
        print("\n웹 배포용 DB는 건너뜁니다 (safety_law_web 폴더 없음).")
        print("웹 배포도 하신다면:  python make_web_db.py 출력경로.db")
        return

    print(f"\n{BAR}\n 웹 배포용 DB 생성\n{BAR}")
    r = subprocess.run([sys.executable, "make_web_db.py"], cwd=BASE)
    if r.returncode != 0:
        print("\n웹용 DB 생성에 실패했습니다. 로컬 DB는 정상이니 다시 시도해 보세요:")
        print("  python make_web_db.py")
        sys.exit(r.returncode)

    # 여기서 안내를 대충 쓰면 사고가 난다. bat이 끝난 자리는 safety_law 폴더라,
    # "safety_law_web 폴더에서"라고만 쓰면 그 자리에서 그대로 git을 치게 된다.
    # (실제로 그렇게 해서 API 키가 엉뚱한 저장소 커밋에 들어갈 뻔했다)
    print(f"\n{BAR}")
    print(" 웹 사이트에 반영하려면 — 아래를 그대로 복사해서 실행하세요.")
    print(" 지금 이 창(safety_law 폴더)에서 git을 치면 안 됩니다.")
    print(f"\n   cd /d \"{web}\"")
    print("   git add -A")
    print('   git commit -m "법령 DB 갱신"')
    print("   git push")
    print("\n push하면 Vercel이 자동으로 재배포합니다 (1~2분).")
    print(BAR)


if __name__ == "__main__":
    main()
