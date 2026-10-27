#!/bin/sh
# 법령 DB 서버 켜기 — 이미 켜져 있으면 껐다가 다시 켠다.
#   sh run_server.sh          (포트 8088)
#   sh run_server.sh 8780     (다른 포트)
# 창을 닫아도 계속 돈다(nohup). 워크스페이스를 껐다 켜면 이것만 다시 실행.
PORT="${1:-8088}"
PY="${PY:-$(command -v python3 || command -v python)}"
cd "$(dirname "$0")" || exit 1

# 전에 켠 서버 끄기 — 같은 포트에 옛 서버가 남아 있으면 새 서버가 못 뜬다
if [ -f she_law.pid ]; then kill "$(cat she_law.pid)" 2>/dev/null; fi
pkill -f "she_law_mcp.py --http" 2>/dev/null
sleep 1

nohup "$PY" she_law_mcp.py --http "0.0.0.0:$PORT" > she_law.log 2>&1 &
echo $! > she_law.pid
sleep 2

if "$PY" -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:$PORT/health', timeout=5).read().decode())"; then
  echo "[OK] 서버 켜짐 — 포트 $PORT"
  echo "     내 워크스페이스에서 연결: claude mcp add --transport http -s user she-law http://127.0.0.1:$PORT/mcp"
  echo "     끄기: kill \$(cat she_law.pid)   기록: she_law.log"
else
  echo "!! 서버가 응답하지 않습니다 — she_law.log 마지막 줄:"
  tail -5 she_law.log
  exit 1
fi
