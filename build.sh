#!/usr/bin/env bash
set -euo pipefail

# Build xray-monitor as a single Linux binary using PyInstaller
# Run on a Linux machine: bash build.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "==> Installing dependencies..."
pip install -e ".[build]" 2>/dev/null || pip install -e . pyinstaller

echo "==> Building binary with PyInstaller..."
pyinstaller \
    --onefile \
    --name xray-monitor \
    --strip \
    --noconfirm \
    --clean \
    --hidden-import=grpc \
    --hidden-import=grpc._cython \
    --hidden-import=psutil \
    --hidden-import=qrcode \
    --hidden-import=textual \
    --hidden-import=rich \
    --collect-data textual \
    src/xray_monitor/__main__.py

echo ""
echo "==> Binary built: dist/xray-monitor"
echo "    Size: $(du -h dist/xray-monitor | cut -f1)"
echo ""
echo "To install on server:"
echo "  scp dist/xray-monitor root@your-server:/usr/local/bin/"
echo "  ssh root@your-server chmod +x /usr/local/bin/xray-monitor"
