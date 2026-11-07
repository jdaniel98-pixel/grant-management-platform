#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
exec python -m streamlit run app.py --server.address=0.0.0.0 --server.port="${PORT:-8501}" --server.headless=true
