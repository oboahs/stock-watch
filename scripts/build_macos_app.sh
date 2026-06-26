#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3.11}"
BUILD_VENV="$ROOT_DIR/.build-venv-macos"

"$PYTHON_BIN" -m venv "$BUILD_VENV"
"$BUILD_VENV/bin/python" -m pip install --upgrade pip setuptools wheel
"$BUILD_VENV/bin/python" -m pip install -r requirements-desktop.txt pyinstaller

rm -rf build dist
"$BUILD_VENV/bin/pyinstaller" --clean --noconfirm packaging/stock_watch_assistant_macos.spec

echo "macOS app built at:"
echo "$ROOT_DIR/dist/Stock Watch Assistant.app"
