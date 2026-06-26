#!/bin/zsh
set -e
cd "$(dirname "$0")/.."
if [ -x ".venv/bin/python" ] && .venv/bin/python -c "import pandas, yaml, requests" >/dev/null 2>&1; then
  .venv/bin/python gui.py
else
  python3.11 gui.py
fi
