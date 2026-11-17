#!/usr/bin/env bash
# Record the FinExtract demo video: a live run on a filing, ending on the configuration slide.
#
#   scripts/demo-video/record-demo.sh <filing.pdf> <output-dir>
#
# Starts the backend (port 8000) on a FRESH database inside <output-dir>, so the filing is a new
# upload and the run happens on camera, and the frontend (port 5173). It then records the
# walkthrough and writes <output-dir>/final/FinExtract_demo.mp4 and .srt. Stop any FinExtract
# already on those ports first.
#
# The run uses whatever model backend/config.local.toml (or config.toml) names, with the key read
# from the environment variable that file's [llm].api_key_env gives. With no model reachable, the
# run is rule-based and the results page says so.
set -euo pipefail
FILING=$(cd "$(dirname "${1:?usage: record-demo.sh <filing.pdf> <output-dir>}")" && pwd)/$(basename "$1")
OUT=${2:?usage: record-demo.sh <filing.pdf> <output-dir>}
mkdir -p "$OUT" && OUT=$(cd "$OUT" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
rm -rf "$OUT/demo.db" "$OUT/objects" "$OUT/rec" "$OUT/final"
mkdir -p "$OUT/objects" "$OUT/rec"

cd "$ROOT/backend"
FINEX_DATABASE_URL="sqlite:///$OUT/demo.db" FINEX_OBJECT_STORE_ROOT="$OUT/objects" \
  python3 -m uvicorn app.main:app --port 8000 > "$OUT/backend.log" 2>&1 &
BACKEND=$!
cd "$ROOT/frontend"
npx vite --port 5173 --strictPort > "$OUT/vite.log" 2>&1 &
FRONTEND=$!
trap 'kill $BACKEND $FRONTEND 2>/dev/null || true' EXIT

for _ in $(seq 1 90); do curl -s http://127.0.0.1:8000/health | grep -q ok && break; sleep 1; done
for _ in $(seq 1 90); do curl -s -o /dev/null http://127.0.0.1:5173 && break; sleep 1; done
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"admin"}' | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('token') or d.get('access_token'))")

NODE_PATH="$ROOT/frontend/node_modules" node "$HERE/record.cjs" http://localhost:5173 "$TOKEN" \
  "$FILING" "$OUT/rec" "$HERE/config_slide.html"
python3 "$HERE/burn.py" "$OUT/rec" "$OUT/final"
echo "Video: $OUT/final/FinExtract_demo.mp4"
