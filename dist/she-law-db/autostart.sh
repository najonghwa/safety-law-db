#!/bin/sh
# 터미널을 열 때 법령 DB 서버가 꺼져 있으면 켜지도록 ~/.bashrc 에 한 줄 등록한다(한 번만 실행).
#   sh autostart.sh           (포트 8088)
#   sh autostart.sh 8780      (다른 포트)
#   sh autostart.sh off       (등록 해제)
# 워크스페이스가 켜질 때 저절로 켜지는 않는다 — 아침에 터미널을 한 번 열면 켜진다. 깃허브 최신본은 sh update.sh 로 받는다.
DIR="$(cd "$(dirname "$0")" && pwd)"
MARK="# she-law autostart"
if [ "$1" = "off" ]; then
  [ -f "$HOME/.bashrc" ] && sed -i "/$MARK/d" "$HOME/.bashrc"
  echo "[OK] 등록 해제 (켜져 있는 서버 끄기: kill \$(cat $DIR/she_law.pid))"
  exit 0
fi
PORT="${1:-8088}"
PY="${PY:-$(command -v python3 || command -v python)}"
LINE="PY=$PY sh $DIR/ensure_server.sh $PORT >/dev/null 2>&1"
grep -q "$MARK" "$HOME/.bashrc" 2>/dev/null || echo "$LINE $MARK" >> "$HOME/.bashrc"
echo "[OK] 터미널을 열 때 서버를 확인하도록 ~/.bashrc 에 한 줄 등록"
PY="$PY" sh "$DIR/ensure_server.sh" "$PORT" && echo "[OK] 지금 서버 상태: 켜짐 (포트 $PORT)"
