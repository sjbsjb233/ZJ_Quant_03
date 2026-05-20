"""metadata/field_registry -- turns schema evolution into queryable data (doc §2.3).

State machine for every field:
    extras --[used >=30d & >=1 consumer]--> promoted --[QA + migration]--> core
                                                                            |
                                                                       deprecated
New fields are auto-registered as 'extras' the first time ingestion sees them.
"""
from __future__ import annotations

import datetime as dt
import json

from .. import ids
from ..schemas import FIELD_REGISTRY
from . import _common as c

_VALID_STATUS = {"extras", "promoted", "core", "deprecated"}
_STATUS_TS = {"promoted": "promoted_at", "core": "core_at", "deprecated": "deprecated_at"}


@c.locked
def register_field(
    table: str,
    field_name: str,
    data_type: str,
    status: str = "extras",
    description: str = "",
    unit: str | None = None,
    first_seen_batch_id: str | None = None,
    first_seen_at: dt.datetime | None = None,
    authoritative_source: str | None = None,
    root=None,
) -> dict:
    """Idempotently register a field. Existing registration is left untouched
    (preserves first_seen provenance)."""
    if status not in _VALID_STATUS:
        raise ValueError(f"invalid status {status!r}")
    rows = c.load(FIELD_REGISTRY, root)
    existing = c.get(FIELD_REGISTRY, rows, (table, field_name))
    if existing is not None:
        return existing
    row = c.blank(FIELD_REGISTRY)
    row.update(
        table_name=table,
        field_name=field_name,
        data_type=data_type,
        status=status,
        description=description,
        unit=unit,
        first_seen_batch_id=first_seen_batch_id,
        first_seen_at=first_seen_at or ids.utc_now(),
        authoritative_source=authoritative_source,
    )
    rows.append(row)
    c.save(FIELD_REGISTRY, rows, root)
    return row


@c.locked
def update_field_status(table: str, field: str, status: str, root=None, **extra) -> dict:
    """Transition status and stamp the matching timestamp column."""
    if status not in _VALID_STATUS:
        raise ValueError(f"invalid status {status!r}")
    rows = c.load(FIELD_REGISTRY, root)
    row = c.require(FIELD_REGISTRY, rows, (table, field))
    row["status"] = status
    ts_col = _STATUS_TS.get(status)
    if ts_col and row.get(ts_col) is None:
        row[ts_col] = ids.utc_now()
    row.update(extra)
    c.save(FIELD_REGISTRY, rows, root)
    return row


@c.locked
def promote_field(
    table: str,
    field: str,
    status: str = "promoted",
    consumers: list[str] | None = None,
    authoritative_source: str | None = None,
    root=None,
) -> dict:
    """Advance a field to promoted/core/deprecated (doc §7.3)."""
    extra: dict = {}
    if consumers is not None:
        extra["consumers"] = json.dumps(consumers)
    if authoritative_source is not None:
        extra["authoritative_source"] = authoritative_source
    return update_field_status(table, field, status, root=root, **extra)


@c.locked
def add_consumer(table: str, field: str, consumer: str, root=None) -> dict:
    rows = c.load(FIELD_REGISTRY, root)
    row = c.require(FIELD_REGISTRY, rows, (table, field))
    current = json.loads(row["consumers"]) if row.get("consumers") else []
    if consumer not in current:
        current.append(consumer)
    row["consumers"] = json.dumps(current)
    c.save(FIELD_REGISTRY, rows, root)
    return row


def get_field(table: str, field: str, root=None) -> dict | None:
    return c.get(FIELD_REGISTRY, c.load(FIELD_REGISTRY, root), (table, field))


def list_fields(table: str | None = None, status: str | None = None, root=None) -> list[dict]:
    rows = c.load(FIELD_REGISTRY, root)
    return [
        r
        for r in rows
        if (table is None or r["table_name"] == table)
        and (status is None or r["status"] == status)
    ]


def core_field_names(table: str, root=None) -> list[str]:
    """Fields promoted to core for a table (drives the migration compat layer)."""
    return [r["field_name"] for r in list_fields(table=table, status="core", root=root)]


# --- time-travel / housekeeping queries (doc §2.3) --------------------------
def fields_as_of(table: str, as_of: dt.datetime | dt.date, root=None) -> list[dict]:
    """Which fields existed (not yet deprecated) at a point in time."""
    as_of = _as_dt(as_of)
    out = []
    for r in list_fields(table=table, root=root):
        seen = r.get("first_seen_at")
        if seen is None or seen > as_of:
            continue
        dep = r.get("deprecated_at")
        if dep is not None and dep <= as_of:
            continue
        out.append(r)
    return out


def stale_extras(table: str | None = None, days: int = 90, root=None) -> list[dict]:
    """Extras fields older than ``days`` that never reached core (cleanup candidates)."""
    cutoff = ids.utc_now() - dt.timedelta(days=days)
    return [
        r
        for r in list_fields(table=table, status="extras", root=root)
        if r.get("first_seen_at") is not None and r["first_seen_at"] < cutoff
    ]


def _as_dt(value: dt.datetime | dt.date) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value
    return dt.datetime(value.year, value.month, value.day)
