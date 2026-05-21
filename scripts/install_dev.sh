#!/usr/bin/env bash
#
# install_dev.sh — install every packages/* and apps/*/backend in editable mode
# into the active venv.
#
# Order matters: packages (core libs) MUST be installed before the apps, because
# an app's backend declares its dependency on the packages by distribution name
# (e.g. quant-web depends on "quant-data>=0.2.0"). Installing a package editable
# first makes that name resolvable from the local working tree instead of PyPI.
#
# Usage:
#   bash scripts/install_dev.sh           # uses whatever `pip` is on PATH
#
set -euo pipefail

PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "${PROJECT_ROOT}"

log() { printf '[install_dev] %s\n' "$*"; }

install_editable() {
  # Install <dir> editable, preferring its [test] extra, falling back to plain.
  local dir="$1"
  [[ -f "${dir}/pyproject.toml" ]] || return 0
  log "installing (editable) ${dir}"
  pip install --quiet -e "${dir}[test]" || pip install --quiet -e "${dir}"
}

# --- step 1: packages (core libs, depended on by the apps) -----------------
for pkg in packages/*/; do
  install_editable "${pkg%/}"
done

# --- step 2: apps (each app's Python distribution lives in <app>/backend) --
for app in apps/*/; do
  [[ -f "${app}backend/pyproject.toml" ]] && install_editable "${app}backend"
done

log "done. Try:  python -c 'import quant_data, quant_web; print(quant_data.__file__)'"
