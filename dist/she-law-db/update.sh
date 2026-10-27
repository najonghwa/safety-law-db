#!/bin/sh
# 갱신 — 깃허브에서 새 DB·코드 받기 → DB 준비 → (서버로 켜 둔 경우만) 서버 다시 켜기.
#   sh update.sh          (서버 포트 8088)
#   sh update.sh 8780     (서버를 다른 포트로 켜 두었다면)
PY="${PY:-$(command -v python3 || command -v python)}"
cd "$(dirname "$0")" || exit 1
git pull --ff-only || { echo "!! git pull 실패 — 이 폴더에서 손으로 고친 파일이 있는지 확인(git status)"; exit 1; }
"$PY" setup_db.py || exit 1
if [ -f she_law.pid ] && kill -0 "$(cat she_law.pid)" 2>/dev/null; then
  sh run_server.sh "$@"
else
  echo "[OK] 갱신 완료 — 새 DB는 바로, 바뀐 코드는 Claude Code 새 대화부터 쓰입니다."
fi
