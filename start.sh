#!/usr/bin/env sh
# Menjalankan SEMPAT di Linux/macOS
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt || exit 1
fi
exec .venv/bin/python run.py --host 0.0.0.0 "$@"
