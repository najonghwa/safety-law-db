#!/bin/sh
# 서버가 응답하지 않으면 켠다(응답하면 아무것도 안 함) — autostart.sh로 등록하면 터미널을 열 때마다 불린다.
#   sh ensure_server.sh [포트]   (기본 8088)
# pid 파일이 아니라 실제 응답(/health)으로 본다 — 워크스페이스를 껐다 켜면 옛 pid 번호를 다른 프로그램이 쓰고 있을 수 있다.
PORT="${1:-8088}"
PY="${PY:-$(command -v python3 || command -v python)}"
cd "$(dirname "$0")" || exit 1
if "$PY" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$PORT/health', timeout=5)" 2>/dev/null; then
  exit 0
fi
sh run_server.sh "$PORT"
