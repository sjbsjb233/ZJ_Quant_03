"""Pre-computed per-table stats for the dashboard (doc §7.4).

Scanning every parquet file to count rows on each dashboard load is slow, so a
periodic job materialises row counts, disk usage, the latest successful batch,
and a freshness verdict into ``metadata.table_stats``. The dashboard then reads
this one tiny table.
"""
from __future__ import annotations

import datetime as dt
import os

from . import ids, storage
from .metadata import _common as c
from .metadata import batches
from .schemas import RAW_TABLES, REFERENCE_TABLES, TABLE_STATS, get_spec

# Freshness thresholds in days (doc §4.1.2). Approximated with calendar days;
# fundamentals/corporate_actions are event-driven so they get a wider window.
_FRESHNESS = {
    "raw.bars_daily": (1, 3),
    "raw.bars_minute": (1, 3),
    "raw.fundamentals": (7, 30),
    "raw.universe": (1, 3),
    "raw.calendar": (180, 365),
    "raw.corporate_actions": (7, 30),
    "reference.symbols": (30, 90),
    "reference.sectors": (90, 365),
}
# Tables that are intentionally low-frequency: show "info" instead of "warn".
_STATIC = {"raw.calendar", "reference.sectors"}


def _disk_bytes(files: list[str]) -> int:
    return sum(os.path.getsize(f) for f in files if os.path.exists(f))


def _freshness(table: str, latest_at: dt.datetime | None) -> str:
    if latest_at is None:
        return "info" if table in _STATIC else "warn"
    age_days = (ids.utc_now() - latest_at).total_seconds() / 86400
    warn, crit = _FRESHNESS.get(table, (7, 30))
    if age_days > crit:
        return "info" if table in _STATIC else "critical"
    if age_days > warn:
        return "info" if table in _STATIC else "warn"
    return "ok"


def compute_table_stats(table: str, root=None) -> dict:
    """Row count / disk / latest batch / freshness for one table."""
    from .reader import DataReader

    spec = get_spec(table)
    reader = DataReader(root=root)
    latest_id = latest_at = None

    if spec.kind == "raw":
        files = storage.list_data_files(spec, root=root)
        succ = [
            b for b in batches.list_batches(target_table=table, status="success", root=root)
        ]
        if succ:
            newest = max(succ, key=lambda b: b.get("finished_at") or b.get("started_at") or dt.datetime.min)
            latest_id = newest["batch_id"]
            latest_at = newest.get("finished_at") or newest.get("started_at")
        try:
            # COUNT(*) in-db -- never materialise the table. ``current_view().num_rows``
            # copies the whole de-duplicated table into Arrow/Python first, so RSS grows
            # linearly with table size and OOM-kills the memory-limited data-keeper on
            # large raw tables (e.g. 18M-row raw.bars_daily). The DuckDB memory limit only
            # bounds the engine, not the to-Python copy, so it does not help here.
            # web-backend switched to count_current_view after the 2026-05-23 incident.
            row_count = reader.count_current_view(spec)
        except Exception:  # noqa: BLE001 - never let a bad file kill the whole refresh
            row_count = None
    else:  # reference: single mutable file, no batch ledger
        path = storage.single_file_path(spec, root=root)
        files = [str(path)] if path.exists() else []
        if path.exists():
            latest_at = dt.datetime.utcfromtimestamp(os.path.getmtime(path))
        read = reader.read_symbols if table == "reference.symbols" else reader.read_sectors
        try:
            row_count = read().num_rows
        except Exception:  # noqa: BLE001
            row_count = None

    return {
        "table_name": table,
        "row_count": row_count,
        "disk_bytes": _disk_bytes(files),
        "file_count": len(files),
        "latest_batch_id": latest_id,
        "latest_batch_at": latest_at,
        "freshness_status": _freshness(table, latest_at),
        "computed_at": ids.utc_now(),
    }


@c.locked
def refresh_all(root=None) -> list[dict]:
    """Recompute stats for every raw + reference table and persist them."""
    tables = list(RAW_TABLES) + list(REFERENCE_TABLES)
    rows = [compute_table_stats(t, root=root) for t in tables]
    c.save(TABLE_STATS, rows, root)
    return rows


def get_stats(root=None) -> list[dict]:
    """Cached stats. Falls back to a live compute if never refreshed."""
    rows = c.load(TABLE_STATS, root)
    if not rows:
        return refresh_all(root=root)
    order = list(RAW_TABLES) + list(REFERENCE_TABLES)
    rows.sort(key=lambda r: order.index(r["table_name"]) if r["table_name"] in order else 99)
    return rows
