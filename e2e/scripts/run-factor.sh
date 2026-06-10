#!/usr/bin/env bash
# Run factor-api against the same demo warehouse the backend seeded.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(cd ../ && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3
STACK="$PWD/.stack"
mkdir -p "$STACK/factor-store"

export QUANT_DATA_ROOT="$STACK/warehouse"
export FACTOR_STORE_ROOT="$STACK/factor-store"
export FACTOR_DB_URL="sqlite:///$STACK/factor-store/factor.db"
export FACTOR_ROLE=api

exec "$PY" -m uvicorn factor_service.main:app --host 127.0.0.1 --port 8100
