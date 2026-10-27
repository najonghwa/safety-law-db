#!/bin/sh
# 갱신 — 깃허브에서 새 DB·코드 받기 → DB 준비 → 서버 다시 켜기.
#   sh update.sh          (포트 8088)
#   sh update.sh 8780     (다른 포트로 켜 두었다면)
PY="${PY:-$(command -v python3 || command -v python)}"
cd "$(dirname "$0")" || exit 1
git pull --ff-only || { echo "!! git pull 실패 — 이 폴더에서 손으로 고친 파일이 있는지 확인(git status)"; exit 1; }
"$PY" setup_db.py || exit 1
sh run_server.sh "$@"
