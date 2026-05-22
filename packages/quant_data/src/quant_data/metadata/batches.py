"""metadata/batches -- the central provenance ledger (doc §2.1).

Every write to raw/ (or, later, derived/) inserts a 'running' row here, then
finalizes it to success/failed. Invalidation + supersession flip metadata only,
so killing a polluted batch never rewrites a Parquet file (doc §1.3).
"""
from __future__ import annotations

import json

from .. import ids
from ..logging import get_logger
from ..schemas import BATCHES
from . import _common as c

_VALID_STATUS = {"running", "success", "failed", "invalidated"}
_log = get_logger("quant_data.governance")


@c.locked
def register_batch(
    source: str,
    target_table: str,
    parameters: dict | None = None,
    notes: str | None = None,
    batch_id: str | None = None,
    root=None,
) -> str:
    """Insert a 'running' row and return its batch_id (doc §7.1 step 1)."""
    rows = c.load(BATCHES, root)
    batch_id = batch_id or ids.new_batch_id()
    if c.get(BATCHES, rows, (batch_id,)) is not None:
        raise ValueError(f"batch_id {batch_id} already exists")

    row = c.blank(BATCHES)
    row.update(
        batch_id=batch_id,
        source=source,
        target_table=target_table,
        started_at=ids.utc_now(),
        status="running",
        parameters=ids.canonical_json(parameters) if parameters is not None else None,
        parameters_hash=ids.parameters_hash(parameters) if parameters is not None else None,
        notes=notes,
    )
    rows.append(row)
    c.save(BATCHES, rows, root)
    return batch_id


@c.locked
def finalize_batch(
    batch_id: str,
    status: str,
    row_count: int | None = None,
    partition_keys: list[str] | None = None,
    error_message: str | None = None,
    root=None,
) -> dict:
    """Close out a batch: success/failed + counts + touched partitions."""
    if status not in _VALID_STATUS:
        raise ValueError(f"invalid status {status!r}")
    rows = c.load(BATCHES, root)
    row = c.require(BATCHES, rows, (batch_id,))
    # state machine: only a running batch may be finalized (doc test T-MB-005)
    if row["status"] != "running":
        raise ValueError(
            f"cannot finalize batch {batch_id}: status is {row['status']!r}, not 'running'"
        )
    row.update(
        finished_at=ids.utc_now(),
        status=status,
        row_count=row_count,
        partition_keys=json.dumps(partition_keys) if partition_keys is not None else None,
        error_message=error_message,
    )
    c.save(BATCHES, rows, root)
    return row


@c.locked
def update_batch(batch_id: str, root=None, **fields) -> dict:
    """Patch arbitrary columns on a batch row."""
    unknown = set(fields) - set(BATCHES.all_names())
    if unknown:
        raise ValueError(f"unknown batch columns: {sorted(unknown)}")
    rows = c.load(BATCHES, root)
    row = c.require(BATCHES, rows, (batch_id,))
    row.update(fields)
    c.save(BATCHES, rows, root)
    return row


@c.locked
def invalidate_batch(batch_id: str, reason: str, root=None) -> dict:
    """Mark a batch invalid so the current view stops returning its rows."""
    rows = c.load(BATCHES, root)
    row = c.require(BATCHES, rows, (batch_id,))
    row.update(status="invalidated", invalidated_at=ids.utc_now(), invalidated_reason=reason)
    c.save(BATCHES, rows, root)
    _log.info("governance.batch.invalidate", batch_id=batch_id, reason=reason)
    return row


@c.locked
def supersede_batch(old_batch_id: str, new_batch_id: str, root=None) -> None:
    """Link a corrective batch to the one it replaces (bidirectional, doc §7.2).

    Both batches must exist (FK), and the link must not create a cycle in the
    superseded_by chain (doc test T-MB-008/T-MB-009).
    """
    if old_batch_id == new_batch_id:
        raise ValueError("a batch cannot supersede itself")
    rows = c.load(BATCHES, root)
    old = c.require(BATCHES, rows, (old_batch_id,))
    new = c.require(BATCHES, rows, (new_batch_id,))

    # walking superseded_by forward from `new` must not reach `old`
    index = {r["batch_id"]: r for r in rows}
    cur, seen = new_batch_id, set()
    while cur is not None and cur not in seen:
        if cur == old_batch_id:
            raise ValueError(
                f"supersede {old_batch_id}->{new_batch_id} would create a cycle"
            )
        seen.add(cur)
        cur = index[cur]["superseded_by"] if cur in index else None

    old["superseded_by"] = new_batch_id
    new["supersedes"] = old_batch_id
    c.save(BATCHES, rows, root)
    _log.info("governance.batch.supersede", old_batch_id=old_batch_id,
              new_batch_id=new_batch_id)


def get_batch(batch_id: str, root=None) -> dict | None:
    return c.get(BATCHES, c.load(BATCHES, root), (batch_id,))


def list_batches(
    target_table: str | None = None, status: str | None = None, root=None
) -> list[dict]:
    rows = c.load(BATCHES, root)
    out = [
        r
        for r in rows
        if (target_table is None or r["target_table"] == target_table)
        and (status is None or r["status"] == status)
    ]
    out.sort(key=lambda r: (r["started_at"] or 0))
    return out


def stale_running(older_than_hours: int = 24, root=None) -> list[dict]:
    """Running batches older than a threshold -- orphans from dead processes
    (doc test T-MB-006). Detection only; an external reaper decides what to do."""
    import datetime as _dt

    cutoff = ids.utc_now() - _dt.timedelta(hours=older_than_hours)
    return [
        r for r in c.load(BATCHES, root)
        if r["status"] == "running" and r["started_at"] is not None and r["started_at"] < cutoff
    ]


def find_by_parameters(
    target_table: str, parameters: dict, statuses=("running", "success"), root=None
) -> list[dict]:
    """Dedup helper: existing batches with identical params (doc §2.1)."""
    h = ids.parameters_hash(parameters)
    return [
        r
        for r in c.load(BATCHES, root)
        if r["target_table"] == target_table
        and r["parameters_hash"] == h
        and r["status"] in statuses
    ]
