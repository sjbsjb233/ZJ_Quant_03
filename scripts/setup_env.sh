#!/usr/bin/env bash
#
# setup_env.sh — provision a per-worktree, isolated Python environment.
#
# Each git worktree gets its own `.venv/` at the worktree root, built from a
# Python 3.13 interpreter. The venv is local to the worktree and is NOT tracked
# by git (`.venv` is in .gitignore). Project dependencies (from pyproject.toml)
# are installed into the venv, not into the global/conda base environment.
#
# This script is idempotent: if a healthy 3.13 venv already exists it exits
# fast. It is safe to run on every session start.
#
# Usage:
#   bash scripts/setup_env.sh            # build / refresh the worktree env
#   FORCE=1 bash scripts/setup_env.sh    # rebuild from scratch
#
set -euo pipefail

REQUIRED_SERIES="3.13"

# --- locate the worktree root (works from any subdirectory) ----------------
PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
VENV_DIR="${PROJECT_ROOT}/.venv"
VENV_PY="${VENV_DIR}/bin/python"
# Sentinel written only after a fully successful build, so an interrupted or
# network-failed install is retried next time instead of being treated as ready.
SENTINEL="${VENV_DIR}/.setup_complete"

log() { printf '[setup_env] %s\n' "$*"; }

# --- fast path: a fully-built venv of the right series already exists ------
if [[ -z "${FORCE:-}" && -x "${VENV_PY}" && -f "${SENTINEL}" ]]; then
  existing="$("${VENV_PY}" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || true)"
  if [[ "${existing}" == "${REQUIRED_SERIES}" ]]; then
    log "env ready: ${VENV_DIR} (Python ${existing})"
    exit 0
  fi
  log "existing venv is Python '${existing:-unknown}', expected ${REQUIRED_SERIES} — rebuilding"
  rm -rf "${VENV_DIR}"
fi
[[ -n "${FORCE:-}" ]] && rm -rf "${VENV_DIR}"

# --- find a base interpreter in the 3.13 series ----------------------------
# Prefer an explicit `python3.13`, then fall back to any python3 that reports
# 3.13. We deliberately build a *fresh, isolated* venv from it rather than
# working inside the global/conda base environment.
pick_python() {
  local candidate ver
  for candidate in python3.13 python3 python; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      ver="$("${candidate}" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || true)"
      if [[ "${ver}" == "${REQUIRED_SERIES}" ]]; then
        command -v "${candidate}"
        return 0
      fi
    fi
  done
  return 1
}

if ! BASE_PY="$(pick_python)"; then
  log "ERROR: no Python ${REQUIRED_SERIES} interpreter found on PATH." >&2
  log "       Install Python ${REQUIRED_SERIES} (or expose it as python3.13) and re-run." >&2
  exit 1
fi

full_ver="$("${BASE_PY}" -c 'import platform;print(platform.python_version())')"
log "building isolated venv from ${BASE_PY} (Python ${full_ver})"
log "target: ${VENV_DIR}"

# Build the venv. --copies copies the interpreter binary into the worktree so
# the env is as self-contained as venv allows (rather than a bare symlink).
"${BASE_PY}" -m venv --copies "${VENV_DIR}"

# --- install tooling + project dependencies into the venv ------------------
# pip upgrade is best-effort: a transient network failure here must not abort
# the build (the venv already has a working pip).
"${VENV_PY}" -m pip install --quiet --upgrade pip setuptools wheel || \
  log "WARNING: could not upgrade pip/setuptools/wheel (network?); continuing"

# monorepo: install all packages/* then apps/*/backend in dependency order.
INSTALL_SCRIPT="${PROJECT_ROOT}/scripts/install_dev.sh"
if [[ -f "${INSTALL_SCRIPT}" ]]; then
  log "running install_dev.sh (packages first, then apps' backends)"
  PATH="${VENV_DIR}/bin:${PATH}" bash "${INSTALL_SCRIPT}"
elif [[ -f "${PROJECT_ROOT}/requirements.txt" ]]; then
  log "installing from requirements.txt"
  "${VENV_PY}" -m pip install --quiet -r "${PROJECT_ROOT}/requirements.txt"
else
  log "WARNING: scripts/install_dev.sh missing — created a bare venv"
fi

# Mark the build complete (records what was provisioned, for debugging).
printf 'python=%s\nbase=%s\nbuilt=%s\n' \
  "${full_ver}" "${BASE_PY}" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "${SENTINEL}"

log "done. Activate with:  source .venv/bin/activate   (or run .venv/bin/python directly)"
