#!/bin/sh
# 갱신 — 깃허브에서 새 DB·코드 받기 → DB 준비 → 바뀐 게 있을 때만 서버 다시 켜기.
#   sh update.sh          (서버 포트 8088)
#   sh update.sh 8780     (서버를 다른 포트로 켜 두었다면)
# autostart.sh를 실행해 두면 평일 아침 7시 40분에 저절로 돈다(기록: update.log).
PY="${PY:-$(command -v python3 || command -v python)}"
cd "$(dirname "$0")" || exit 1
OLD="$(git rev-parse HEAD 2>/dev/null)"
git pull --ff-only || { echo "!! git pull 실패 — 이 폴더에서 손으로 고친 파일이 있는지 확인(git status)"; exit 1; }
NEW="$(git rev-parse HEAD 2>/dev/null)"
"$PY" setup_db.py || exit 1          # DB 압축이 바뀌었으면 풀어서 바꿔 끼운다(켜진 서버는 다음 질문부터 새 DB)
if [ -f she_law.pid ] && kill -0 "$(cat she_law.pid)" 2>/dev/null; then
  if [ "$OLD" != "$NEW" ]; then
    sh run_server.sh "$@"            # 코드가 바뀌었으면 서버를 새 코드로 다시 켠다
  else
    echo "[OK] 바뀐 코드 없음 — 서버 그대로 ($(date '+%Y-%m-%d %H:%M'))"
  fi
else
  echo "[OK] 갱신 완료 — 새 DB는 바로, 바뀐 코드는 다음에 서버를 켤 때부터 쓰입니다."
fi
