#!/bin/sh
# 워크스페이스가 켜지면 법령 DB 서버도 저절로 켜지게 등록한다(한 번만 실행).
#   sh autostart.sh           (포트 8088)
#   sh autostart.sh 8780      (다른 포트)
#   sh autostart.sh off       (자동 시작 해제)
# 1) cron: 워크스페이스가 켜질 때 + 5분마다 서버를 확인해 꺼져 있으면 켠다
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
  (crontab -l 2>/dev/null | grep -v "$MARK"; echo "@reboot $LINE $MARK"; echo "*/5 * * * * $LINE $MARK") | crontab -
  echo "[OK] cron 등록 — 워크스페이스가 켜질 때, 그리고 5분마다 서버를 확인해 꺼져 있으면 켭니다"
else
  echo "! crontab 이 없습니다 — 터미널을 열 때 켜지도록만 등록합니다"
fi
grep -q "$MARK" "$HOME/.bashrc" 2>/dev/null || echo "$LINE $MARK" >> "$HOME/.bashrc"
echo "[OK] 터미널을 열 때도 확인하도록 ~/.bashrc 에 한 줄 추가"
PY="$PY" sh "$DIR/ensure_server.sh" "$PORT" && echo "[OK] 지금 서버 상태: 켜짐 (포트 $PORT)"
