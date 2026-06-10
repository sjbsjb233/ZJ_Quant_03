#!/usr/bin/env bash
# Seed (once) and run the web backend for the e2e suite. State lives in
# e2e/.stack so repeated runs reuse the same demo warehouse.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(cd ../ && pwd)"
PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python3
STACK="$PWD/.stack"
mkdir -p "$STACK/logs"

export QUANT_DATA_ROOT="$STACK/warehouse"
export AUDIT_DB_URL="sqlite:///$STACK/audit.db"
export SEED_DEFAULT_USERS=true
export LOG_FILE_DIR="$STACK/logs"
export FACTOR_API_BASE="http://localhost:8100"
export MONITOR_ENABLED=false

if [ ! -d "$STACK/warehouse" ]; then
  "$PY" -m quant_web.seed_demo "$STACK/warehouse"
fi

exec "$PY" -m uvicorn quant_web.main:app --host 127.0.0.1 --port 8000
