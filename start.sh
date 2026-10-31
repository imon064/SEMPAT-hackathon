#!/usr/bin/env sh
# Menjalankan SEMPAT di Linux/macOS
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv || exit 1
fi
.venv/bin/python -c "import fastapi, uvicorn, cryptography, numpy, scipy" 2>/dev/null || \
  .venv/bin/python -m pip install -r requirements.txt || exit 1
exec .venv/bin/python run.py --host 0.0.0.0 "$@"
