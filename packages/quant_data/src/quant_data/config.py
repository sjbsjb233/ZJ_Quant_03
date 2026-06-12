"""Runtime configuration: data-root resolution + global constants."""
from __future__ import annotations

import contextvars
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

# DuckDB CPU quota (内存治理方案 WP6). DuckDB defaults to "all cores" PER
# CONNECTION; with N concurrent heavy tasks each holding a connection that is
# an N× CPU oversell -- health checks and light requests starve (the 2026-06-01
# 7.9s-lookup incident, and one of the 06-04 假死 amplifiers). Each connection
# gets cores ÷ heavy-task-slots so the whole process is sized to 1× the cgroup
# cpuset. ``QUANT_DUCKDB_THREADS`` overrides the计算 ("auto" or unset = derive);
# ``QUANT_HEAVY_TASK_SLOTS`` is the divisor (size together with
# FACTOR_CONCURRENCY / the keeper worker pool).
DUCKDB_THREADS_ENV = "QUANT_DUCKDB_THREADS"
HEAVY_TASK_SLOTS_ENV = "QUANT_HEAVY_TASK_SLOTS"
DEFAULT_HEAVY_TASK_SLOTS = 4

# 分批原语 (内存治理方案 WP4): tables whose full-window reads MUST go through
# quant_data.chunking.chunked_panel (static trigger -- a配置表, never a runtime
# load probe). Daily bars after column projection fit in one read; minute bars
# (rows ×240) do not.
CHUNK_TABLES_ENV = "CHUNK_TABLES"
DEFAULT_CHUNK_TABLES = "raw.bars_minute"

# 迁移期扫尾 (内存治理方案 WP2): when "1", reads that materialise > 1GB without
# a column projection emit ``reader.projection.missing`` so leftover full-column
# call sites can be found and annotated.
READER_PROJECTION_LOG_ENV = "QUANT_READER_PROJECTION_LOG"

# Per-task thread override (内存治理方案 WP5 独占 CPU 提升): an exclusive
# admission grant may raise the DuckDB thread quota for connections opened by
# that task's thread. contextvars give per-thread/-task isolation for free.
_thread_override: contextvars.ContextVar[int | None] = contextvars.ContextVar(
    "quant_duckdb_thread_override", default=None
)


def available_cores() -> int:
    """CPU cores this process may use -- cgroup cpuset aware on Linux."""
    try:
        return max(1, len(os.sched_getaffinity(0)))  # type: ignore[attr-defined]
    except AttributeError:  # macOS / non-Linux dev machines
        return max(1, os.cpu_count() or 1)


def duckdb_threads() -> int:
    """Threads for one DuckDB connection: override > env > cores ÷ heavy slots."""
    override = _thread_override.get()
    if override:
        return max(1, int(override))
    env = (os.environ.get(DUCKDB_THREADS_ENV) or "").strip().lower()
    if env and env != "auto":
        return max(1, int(env))
    slots = int(os.environ.get(HEAVY_TASK_SLOTS_ENV, DEFAULT_HEAVY_TASK_SLOTS))
    return max(1, available_cores() // max(1, slots))


def set_duckdb_thread_override(n: int | None) -> contextvars.Token:
    """Raise (or clear) the thread quota for connections opened on this
    thread/task -- used by exclusive admission grants (WP5). Returns the reset
    token; pass it to :func:`reset_duckdb_thread_override` in a ``finally``."""
    return _thread_override.set(int(n) if n else None)


def reset_duckdb_thread_override(token: contextvars.Token) -> None:
    try:
        _thread_override.reset(token)
    except (ValueError, LookupError):  # pragma: no cover - foreign-context token
        _thread_override.set(None)


def chunk_tables() -> frozenset[str]:
    """Tables that must be read through the chunked_panel primitive (WP4)."""
    raw = os.environ.get(CHUNK_TABLES_ENV, DEFAULT_CHUNK_TABLES)
    return frozenset(t.strip() for t in raw.split(",") if t.strip())


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
    # CPU quota (WP6): per-connection threads sized so N connections ≈ 1× the
    # cgroup cpuset instead of N× (DuckDB's own default is all cores each).
    con.execute(f"PRAGMA threads={int(duckdb_threads())}")
    tmp = os.environ.get(DUCKDB_TEMP_DIR_ENV)
    if tmp:
        os.makedirs(tmp, exist_ok=True)  # DuckDB won't create the dir itself
        con.execute(f"PRAGMA temp_directory={_sql_str_lit(tmp)}")
    return con
