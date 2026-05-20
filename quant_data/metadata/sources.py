"""metadata/sources -- data-source registry (doc §2.2).

Drives two behaviours at read time: multi-source conflict resolution by
``priority`` (doc §6.1) and column renaming via ``field_mapping`` at ingest
time (doc §7.1 step 3). Switching the primary source is just a priority bump,
so business code never changes (doc §7.4).
"""
from __future__ import annotations

import json

from .. import ids
from ..schemas import SOURCES
from . import _common as c

_VALID_CATEGORY = {"external_api", "manual", "internal"}


@c.locked
def register_source(
    source_id: str,
    display_name: str | None = None,
    category: str = "external_api",
    priority: int = 100,
    is_active: bool = True,
    field_mapping: dict | None = None,
    rate_limit_per_min: int | None = None,
    auth_method: str | None = None,
    endpoint: str | None = None,
    description: str | None = None,
    root=None,
) -> dict:
    """Insert or replace a source registration."""
    if category not in _VALID_CATEGORY:
        raise ValueError(f"invalid category {category!r}")
    rows = c.load(SOURCES, root)
    row = c.blank(SOURCES)
    row.update(
        source_id=source_id,
        display_name=display_name or source_id,
        category=category,
        priority=int(priority),
        is_active=bool(is_active),
        field_mapping=ids.canonical_json(field_mapping) if field_mapping is not None else None,
        rate_limit_per_min=rate_limit_per_min,
        auth_method=auth_method,
        endpoint=endpoint,
        description=description,
        _updated_at=ids.utc_now(),
    )
    c.upsert(SOURCES, rows, row)
    c.save(SOURCES, rows, root)
    return row


@c.locked
def update_source(source_id: str, root=None, **fields) -> dict:
    unknown = set(fields) - set(SOURCES.all_names())
    if unknown:
        raise ValueError(f"unknown source columns: {sorted(unknown)}")
    if "field_mapping" in fields and isinstance(fields["field_mapping"], (dict, list)):
        fields["field_mapping"] = ids.canonical_json(fields["field_mapping"])
    rows = c.load(SOURCES, root)
    row = c.require(SOURCES, rows, (source_id,))
    row.update(fields)
    row["_updated_at"] = ids.utc_now()
    c.save(SOURCES, rows, root)
    return row


@c.locked
def update_source_priority(source_id: str, priority: int, root=None) -> dict:
    return update_source(source_id, priority=int(priority), root=root)


@c.locked
def set_field_mapping(source_id: str, mapping: dict, root=None) -> dict:
    return update_source(source_id, field_mapping=ids.canonical_json(mapping), root=root)


def get_field_mapping(source_id: str, target_table: str, root=None) -> dict | None:
    row = get_source(source_id, root)
    if row is None or not row.get("field_mapping"):
        return None
    full = json.loads(row["field_mapping"])
    return full.get(target_table)


def get_source(source_id: str, root=None) -> dict | None:
    return c.get(SOURCES, c.load(SOURCES, root), (source_id,))


def list_sources(active_only: bool = False, root=None) -> list[dict]:
    rows = c.load(SOURCES, root)
    if active_only:
        rows = [r for r in rows if r["is_active"]]
    rows.sort(key=lambda r: -r["priority"])
    return rows


def priority_map(root=None) -> dict[str, int]:
    """source_id -> priority for active sources (used by the current view)."""
    return {r["source_id"]: r["priority"] for r in list_sources(active_only=True, root=root)}
