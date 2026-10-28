#!/bin/sh
# 워크스페이스가 켜지면 법령 DB 서버도 저절로 켜지게 등록한다(한 번만 실행).
#   sh autostart.sh           (포트 8088)
#   sh autostart.sh 8780      (다른 포트)
#   sh autostart.sh off       (자동 시작 해제)
# 1) cron: 워크스페이스가 켜질 때 + 5분마다 서버를 확인해 꺼져 있으면 켠다, 평일 07:40 깃허브 최신본 받기
# 2) 터미널을 열 때도 같은 확인(cron이 안 도는 환경 대비)
DIR="$(cd "$(dirname "$0")" && pwd)"
MARK="# she-law autostart"
if [ "$1" = "off" ]; then
  crontab -l 2>/dev/null | grep -v "$MARK" | crontab - 2>/dev/null
  [ -f "$HOME/.bashrc" ] && sed -i "/$MARK/d" "$HOME/.bashrc"
  echo "[OK] 자동 시작 해제 (켜져 있는 서버 끄기: kill \$(cat $DIR/she_law.pid))"
  exit 0
fi
PORT="${1:-8088}"
PY="${PY:-$(command -v python3 || command -v python)}"
LINE="PY=$PY sh $DIR/ensure_server.sh $PORT >/dev/null 2>&1"

if command -v crontab >/dev/null 2>&1; then
  UPD="PY=$PY sh $DIR/update.sh $PORT >> $DIR/update.log 2>&1"
  (crontab -l 2>/dev/null | grep -v "$MARK"; echo "@reboot $LINE $MARK"; echo "*/5 * * * * $LINE $MARK";
   echo "40 7 * * 1-5 $UPD $MARK") | crontab -
  echo "[OK] cron 등록 — 워크스페이스가 켜질 때·5분마다 서버 확인, 평일 07:40 깃허브 최신본 받기(update.log)"
  # crontab 명령이 있어도 cron 프로그램이 안 돌면 등록만 되고 실행되지 않는다
  if command -v pgrep >/dev/null 2>&1; then
    if pgrep -x cron >/dev/null 2>&1 || pgrep -x crond >/dev/null 2>&1; then
      echo "[OK] cron 동작 중 — 워크스페이스가 켜지면 서버도 같이 켜집니다"
    else
      echo "! cron 이 돌고 있지 않습니다 — 워크스페이스가 켜질 때 자동으로는 안 켜지고, 터미널을 열 때 켜집니다"
    fi
  fi
else
  echo "! crontab 이 없습니다 — 터미널을 열 때 켜지도록만 등록합니다"
fi
grep -q "$MARK" "$HOME/.bashrc" 2>/dev/null || echo "$LINE $MARK" >> "$HOME/.bashrc"
echo "[OK] 터미널을 열 때도 확인하도록 ~/.bashrc 에 한 줄 추가"
PY="$PY" sh "$DIR/ensure_server.sh" "$PORT" && echo "[OK] 지금 서버 상태: 켜짐 (포트 $PORT)"
