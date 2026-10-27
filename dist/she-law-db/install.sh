#!/bin/sh
# 내 워크스페이스에 법령 DB 도구 설치 — 늘 켜 둘 서버 없이, Claude Code가 켜질 때 같이 켜진다.
#   git clone https://github.com/najonghwa/safety-law-db.git
#   cd safety-law-db/dist/she-law-db
#   sh install.sh
# 갱신은 같은 폴더에서 sh update.sh
PY="${PY:-$(command -v python3 || command -v python)}"
SCOPE="${SCOPE:-user}"
cd "$(dirname "$0")" || exit 1
DIR="$(pwd)"

"$PY" setup_db.py || exit 1

if command -v claude >/dev/null 2>&1; then
  # 예전에 서버 주소로 등록한 she-law가 있으면 지우고 내 워크스페이스 것으로 바꾼다
  claude mcp remove she-law -s "$SCOPE" >/dev/null 2>&1
  claude mcp add -s "$SCOPE" she-law -- "$PY" "$DIR/she_law_mcp.py" || exit 1
  echo "[OK] 설치 완료 — Claude Code 새 대화부터 she-law 도구가 붙습니다. 확인: claude mcp list"
else
  echo "!! claude 명령이 없습니다. 쓰는 폴더에 .mcp.json 파일을 아래 한 줄로 만들면 같은 효과입니다:"
  echo "{\"mcpServers\": {\"she-law\": {\"command\": \"$PY\", \"args\": [\"$DIR/she_law_mcp.py\"]}}}"
fi
