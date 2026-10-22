#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m streamlit run app.py --server.address 0.0.0.0 --server.port "${PORT:-8501}"
