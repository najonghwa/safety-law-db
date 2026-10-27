#!/bin/sh
# 서버가 꺼져 있으면 켠다(켜져 있으면 아무것도 안 함) — autostart.sh가 워크스페이스 시작·5분마다 부른다.
#   sh ensure_server.sh [포트]   (기본 8088)
PORT="${1:-8088}"
cd "$(dirname "$0")" || exit 1
if [ -f she_law.pid ] && kill -0 "$(cat she_law.pid)" 2>/dev/null; then
  exit 0
fi
sh run_server.sh "$PORT"
