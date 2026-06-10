#!/usr/bin/env bash
# 一键压测: 复用 e2e 栈脚本拉起 web-backend + factor-api, 等健康后跑负载, 结束后清理。
# 用法: bash stress/run_stress.sh [--users 30] [--duration 30] [--scenario mixed]
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
[ -x "$PY" ] || PY=python3

cleanup() {
  [ -n "${BACK_PID:-}" ] && kill "$BACK_PID" 2>/dev/null || true
  [ -n "${FACT_PID:-}" ] && kill "$FACT_PID" 2>/dev/null || true
}
trap cleanup EXIT

started_backend=""
if ! curl -fsS http://localhost:8000/api/v1/health >/dev/null 2>&1; then
  bash e2e/scripts/run-backend.sh >/tmp/stress-backend.log 2>&1 &
  BACK_PID=$!
  started_backend=1
fi
if ! curl -fsS http://localhost:8100/health >/dev/null 2>&1; then
  bash e2e/scripts/run-factor.sh >/tmp/stress-factor.log 2>&1 &
  FACT_PID=$!
fi

for i in $(seq 1 60); do
  curl -fsS http://localhost:8000/api/v1/health >/dev/null 2>&1 \
    && curl -fsS http://localhost:8100/health >/dev/null 2>&1 && break
  sleep 1
  [ "$i" = 60 ] && { echo "stack failed to start"; exit 2; }
done

"$PY" stress/load_web_api.py "$@"
