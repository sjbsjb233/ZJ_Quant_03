"""Metadata governance ledgers (doc §2)."""
from __future__ import annotations

import datetime as dt

import pytest

from quant_data.metadata import batches, field_registry, sources


# --- batches ----------------------------------------------------------------
def test_register_then_finalize(root):
    bid = batches.register_batch("akshare", "raw.bars_daily", parameters={"a": 1}, root=root)
    b = batches.get_batch(bid, root=root)
    assert b["status"] == "running" and b["parameters_hash"]
    batches.finalize_batch(bid, "success", row_count=42, partition_keys=["year=2025/month=1"], root=root)
    b = batches.get_batch(bid, root=root)
    assert b["status"] == "success" and b["row_count"] == 42 and b["finished_at"] is not None


def test_invalidate_sets_reason_and_time(root):
    bid = batches.register_batch("akshare", "raw.bars_daily", root=root)
    batches.invalidate_batch(bid, "bad units", root=root)
    b = batches.get_batch(bid, root=root)
    assert b["status"] == "invalidated" and b["invalidated_reason"] == "bad units"
    assert b["invalidated_at"] is not None


def test_supersede_is_bidirectional(root):
    old = batches.register_batch("akshare", "raw.bars_daily", root=root)
    new = batches.register_batch("akshare", "raw.bars_daily", root=root)
    batches.supersede_batch(old, new, root=root)
    assert batches.get_batch(old, root=root)["superseded_by"] == new
    assert batches.get_batch(new, root=root)["supersedes"] == old


def test_find_by_parameters_dedup(root):
    params = {"symbols": ["A"], "start": "2025-01-01"}
    bid = batches.register_batch("akshare", "raw.bars_daily", parameters=params, root=root)
    batches.finalize_batch(bid, "success", root=root)
    hits = batches.find_by_parameters("raw.bars_daily", params, root=root)
    assert [h["batch_id"] for h in hits] == [bid]
    assert batches.find_by_parameters("raw.bars_daily", {"other": 1}, root=root) == []


def test_register_duplicate_batch_id_rejected(root):
    bid = batches.register_batch("akshare", "raw.bars_daily", root=root)
    with pytest.raises(ValueError):
        batches.register_batch("akshare", "raw.bars_daily", batch_id=bid, root=root)


# --- sources ----------------------------------------------------------------
def test_default_akshare_registered(root):
    s = sources.get_source("akshare", root=root)
    assert s and s["priority"] == 100 and s["is_active"]


def test_priority_map_only_active(root):
    sources.register_source("tushare_pro", priority=50, root=root)
    sources.register_source("dead", priority=999, is_active=False, root=root)
    pm = sources.priority_map(root=root)
    assert pm == {"akshare": 100, "tushare_pro": 50}


def test_update_priority_and_field_mapping(root):
    sources.update_source_priority("akshare", 5, root=root)
    assert sources.get_source("akshare", root=root)["priority"] == 5
    m = sources.get_field_mapping("akshare", "raw.bars_daily", root=root)
    assert m["日期"] == "date" and m["收盘"] == "close"


# --- field_registry ---------------------------------------------------------
def test_register_is_idempotent_and_preserves_first_seen(root):
    r1 = field_registry.register_field("raw.fundamentals", "roe", "DOUBLE",
                                       first_seen_batch_id="b1", root=root)
    r2 = field_registry.register_field("raw.fundamentals", "roe", "DOUBLE",
                                       first_seen_batch_id="b2", root=root)
    assert r2["first_seen_batch_id"] == "b1"  # unchanged
    assert r1["status"] == "extras"


def test_promotion_state_machine_timestamps(root):
    field_registry.register_field("raw.fundamentals", "roe", "DOUBLE", root=root)
    field_registry.promote_field("raw.fundamentals", "roe", "promoted",
                                 consumers=["factor.roe"], root=root)
    f = field_registry.get_field("raw.fundamentals", "roe", root=root)
    assert f["status"] == "promoted" and f["promoted_at"] is not None
    field_registry.update_field_status("raw.fundamentals", "roe", "core", root=root)
    f = field_registry.get_field("raw.fundamentals", "roe", root=root)
    assert f["status"] == "core" and f["core_at"] is not None


def test_fields_as_of_time_travel(root):
    field_registry.register_field("raw.fundamentals", "old", "DOUBLE",
                                  first_seen_at=dt.datetime(2024, 1, 1), root=root)
    field_registry.register_field("raw.fundamentals", "new", "DOUBLE",
                                  first_seen_at=dt.datetime(2025, 1, 1), root=root)
    field_registry.update_field_status("raw.fundamentals", "old", "deprecated", root=root)
    field_registry.get_field("raw.fundamentals", "old", root=root)["deprecated_at"]
    names = {f["field_name"] for f in field_registry.fields_as_of(
        "raw.fundamentals", dt.datetime(2024, 6, 1), root=root)}
    assert names == {"old"}  # 'new' not seen yet, 'old' not yet deprecated


def test_stale_extras_query(root):
    field_registry.register_field("raw.fundamentals", "stale", "DOUBLE",
                                  first_seen_at=dt.datetime(2020, 1, 1), root=root)
    field_registry.register_field("raw.fundamentals", "fresh", "DOUBLE", root=root)
    stale = {f["field_name"] for f in field_registry.stale_extras("raw.fundamentals", 90, root=root)}
    assert stale == {"stale"}


def test_add_consumer(root):
    field_registry.register_field("raw.fundamentals", "roe", "DOUBLE", root=root)
    field_registry.add_consumer("raw.fundamentals", "roe", "factor.a", root=root)
    field_registry.add_consumer("raw.fundamentals", "roe", "factor.a", root=root)  # dedup
    import json
    f = field_registry.get_field("raw.fundamentals", "roe", root=root)
    assert json.loads(f["consumers"]) == ["factor.a"]
