#!/usr/bin/env bash
# install-service.sh — Install the friday-image-pipeline systemd user service
# Runs the pipeline Web UI on login, waits for ComfyUI to be ready first.
#
# Usage: ./install-service.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_FILE="$SCRIPT_DIR/friday-image-pipeline.service"
SYSTEMD_DIR="$HOME/.config/systemd/user"

echo "📦 Installing friday-image-pipeline.service..."

mkdir -p "$SYSTEMD_DIR"
cp "$SERVICE_FILE" "$SYSTEMD_DIR/friday-image-pipeline.service"

systemctl --user daemon-reload
systemctl --user enable friday-image-pipeline.service

echo ""
echo "✅ Service installed + enabled."
echo ""
echo "Commands:"
echo "  systemctl --user start friday-image-pipeline   # start now"
echo "  systemctl --user status friday-image-pipeline  # check status"
echo "  journalctl --user -u friday-image-pipeline -f  # tail logs"
echo ""
echo "The UI will start automatically with your session and restart on failure."
echo "UI: http://localhost:8765"
