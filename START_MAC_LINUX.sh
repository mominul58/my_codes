#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  bash scripts/setup.sh
fi
exec .venv/bin/python -m streamlit run app.py --server.address 127.0.0.1
