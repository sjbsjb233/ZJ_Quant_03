"""Runtime configuration: data-root resolution + global constants."""
from __future__ import annotations

import os
from pathlib import Path

import duckdb

# Default on-disk layout (doc §2): data/{raw,derived,reference,metadata}/...
_ENV_VAR = "QUANT_DATA_ROOT"
_DEFAULT_ROOT = Path.cwd() / "data"

# Top-level namespaces. derived/ is a placeholder this period (doc §4).
NAMESPACES = ("raw", "derived", "reference", "metadata")

# derived/ sub-directories reserved now so the factor layer can slot in later
# without a schema migration (doc §4.1).
DERIVED_PLACEHOLDER_DIRS = (
    "derived/factors_daily",
    "derived/factors_minute",
    "derived/signals_daily",
    "derived/signals_minute",
    "derived/factor_metrics",
)

# Supported downsample targets for read_bars_minute (doc §6.4).
# value = DuckDB INTERVAL string for time_bucket().
MINUTE_FREQUENCIES: dict[str, str] = {
    "1m": "1 minute",
    "5m": "5 minutes",
    "15m": "15 minutes",
    "30m": "30 minutes",
    "1h": "1 hour",
}

# A-share trading sessions, used by minute-bar timestamp validation (doc §3.2).
A_SHARE_SESSIONS = (("09:30", "11:30"), ("13:00", "15:00"))

# Audit retention window before compaction may physically remove dead files
# (doc §1.3 / §9.4). Removal is always manual.
AUDIT_RETENTION_DAYS = 90

# Field-promotion thresholds (doc §1.2 / §2.3).
PROMOTE_MIN_AGE_DAYS = 30

# DuckDB memory guardrails. DuckDB defaults to "80% of RAM" -- in a container
# without a mem_limit that turns one runaway full-table scan into a host OOM
# (the bug behind the 2026-05-23 OOM incident and the 2026-06-01 segfault /
# core-dump that filled "/"). With a finite cap the engine spills to disk when
# a temp dir is set, and raises a clean error otherwise -- both far better than
# getting OOM-killed or core-dumping. Tunable via env so ops can size per host.
DUCKDB_MEMORY_LIMIT_ENV = "QUANT_DUCKDB_MEMORY_LIMIT"
DUCKDB_TEMP_DIR_ENV = "QUANT_DUCKDB_TEMP_DIR"
DEFAULT_DUCKDB_MEMORY_LIMIT = "2GB"


def data_root(root: str | os.PathLike | None = None) -> Path:
    """Resolve the data root: explicit arg > $QUANT_DATA_ROOT > ./data."""
    if root is not None:
        return Path(root)
    env = os.environ.get(_ENV_VAR)
    if env:
        return Path(env)
    return _DEFAULT_ROOT


def _sql_str_lit(v: str) -> str:
    """Single-quote a value for a PRAGMA literal (escape embedded quotes) so a
    stray quote in an env var can't break out or silently set the wrong value."""
    return "'" + str(v).replace("'", "''") + "'"


def connect_duckdb() -> duckdb.DuckDBPyConnection:
    """The one sanctioned way to open a raw DuckDB connection in the data layer.

    Every engine -- reader, compaction, migration, invariants, chart explorer --
    routes through here so they all inherit the same ``memory_limit`` /
    ``temp_directory`` caps. Bypassing it re-opens the host-OOM hole.
    """
    con = duckdb.connect()
    mem = os.environ.get(DUCKDB_MEMORY_LIMIT_ENV, DEFAULT_DUCKDB_MEMORY_LIMIT)
    con.execute(f"PRAGMA memory_limit={_sql_str_lit(mem)}")
    tmp = os.environ.get(DUCKDB_TEMP_DIR_ENV)
    if tmp:
        os.makedirs(tmp, exist_ok=True)  # DuckDB won't create the dir itself
        con.execute(f"PRAGMA temp_directory={_sql_str_lit(tmp)}")
    return con
