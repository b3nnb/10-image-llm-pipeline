#!/usr/bin/env bash
# Start the Friday Image Pipeline Web UI
# Usage: ./start-ui.sh [port]
set -e

PORT=${1:-8765}
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ComfyUI URL — default to localhost:8188, override with env
export COMFY_URL=${COMFY_URL:-"http://127.0.0.1:8188"}
export PIPELINE_OUTPUT_DIR=${PIPELINE_OUTPUT_DIR:-"$SCRIPT_DIR/output"}

echo "🎨 Starting Friday Image Pipeline UI"
echo "   UI:      http://localhost:$PORT"
echo "   ComfyUI: $COMFY_URL"
echo "   Output:  $PIPELINE_OUTPUT_DIR"

cd "$SCRIPT_DIR"
exec python3 server.py --port "$PORT"
