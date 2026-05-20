"""Metadata table state-machine + integrity (test plan §2.9-§2.11)."""
from __future__ import annotations

import datetime as dt

import pytest

from quant_data import ingest, storage
from quant_data.metadata import batches, field_registry, sources
from quant_data.schemas import SOURCES

from .conftest import daily_bar

pytestmark = pytest.mark.unit


# --- §2.9 batches -----------------------------------------------------------
def test_mb005_finalize_twice_rejected(root):
    bid = batches.register_batch("akshare", "raw.bars_daily", root=root)
    batches.finalize_batch(bid, "success", row_count=1, root=root)
    with pytest.raises(ValueError):  # already finalized -> not 'running'
        batches.finalize_batch(bid, "failed", error_message="x", root=root)


def test_mb008_supersede_nonexistent_rejected(root):
    bid = batches.register_batch("akshare", "raw.bars_daily", root=root)
    batches.finalize_batch(bid, "success", row_count=1, root=root)
    with pytest.raises(KeyError):
        batches.supersede_batch(bid, "does-not-exist", root=root)


def test_mb009_supersede_cycle_rejected(root):
    a = batches.register_batch("akshare", "raw.bars_daily", root=root)
    b = batches.register_batch("akshare", "raw.bars_daily", root=root)
    for x in (a, b):
        batches.finalize_batch(x, "success", row_count=1, root=root)
    batches.supersede_batch(a, b, root=root)
    with pytest.raises(ValueError):  # b->a would close the loop
        batches.supersede_batch(b, a, root=root)


def test_mb006_stale_running_detected(root):
    fresh = batches.register_batch("akshare", "raw.bars_daily", root=root)
    old = batches.register_batch("akshare", "raw.bars_daily", root=root)
    batches.update_batch(old, started_at=dt.datetime(2000, 1, 1), root=root)
    stale = {r["batch_id"] for r in batches.stale_running(older_than_hours=24, root=root)}
    assert stale == {old} and fresh not in stale


def test_mb010_parameters_hash_dedup_detection(root):
    p = {"symbols": ["A"], "start": "2024-01-01"}
    bid = batches.register_batch("akshare", "raw.bars_daily", parameters=p, root=root)
    batches.finalize_batch(bid, "success", row_count=1, root=root)
    assert [h["batch_id"] for h in batches.find_by_parameters("raw.bars_daily", p, root=root)] == [bid]


# --- §2.10 sources ----------------------------------------------------------
def test_ms003_priority_switch_persisted(root):
    sources.update_source_priority("akshare", 7, root=root)
    assert sources.get_source("akshare", root=root)["priority"] == 7


def test_ms006_corrupt_field_mapping_fails_fast(root):
    # inject invalid JSON into a source's field_mapping, then ingest with mapping
    sources.register_source("badsrc", priority=10, root=root)
    rows = storage.read_rows(SOURCES, root=root)
    for r in rows:
        if r["source_id"] == "badsrc":
            r["field_mapping"] = "{not valid json"
    storage.write_rows(SOURCES, rows, root=root)
    with pytest.raises(Exception):
        ingest.ingest_bars_daily(records=[daily_bar(dt.date(2024, 1, 2), symbol="A")],
                                 source="badsrc", apply_mapping=True, root=root)


# --- §2.11 field_registry ---------------------------------------------------
def test_fr002_same_field_two_tables_independent(root):
    field_registry.register_field("raw.fundamentals", "roe", "DOUBLE", root=root)
    field_registry.register_field("raw.universe", "roe", "DOUBLE", root=root)
    field_registry.update_field_status("raw.fundamentals", "roe", "core", root=root)
    assert field_registry.get_field("raw.fundamentals", "roe", root=root)["status"] == "core"
    assert field_registry.get_field("raw.universe", "roe", root=root)["status"] == "extras"


def test_fr004_direct_extras_to_core_allowed(root):
    # the registry does not force passing through 'promoted' (documented behaviour)
    field_registry.register_field("raw.fundamentals", "x", "DOUBLE", root=root)
    f = field_registry.update_field_status("raw.fundamentals", "x", "core", root=root)
    assert f["status"] == "core" and f["core_at"] is not None
