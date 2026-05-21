"""Manual-only compaction/vacuum (doc §1.3, §9.4)."""
from __future__ import annotations

import datetime as dt

from quant_data import compaction, ingest, storage
from quant_data.metadata import batches
from quant_data.schemas import get_spec

from .conftest import daily_bar

D = dt.date(2025, 11, 3)


def test_vacuum_dry_run_does_not_delete(root):
    bad = ingest.ingest_bars_daily(records=[daily_bar(D)], root=root)
    batches.invalidate_batch(bad.batch_id, "x", root=root)
    before = storage.list_data_files(get_spec("raw.bars_daily"), root=root)
    plan = compaction.vacuum("raw.bars_daily", older_than_days=0, confirm=False, root=root)
    assert plan["count"] == 1 and "manual only" in plan["note"]
    assert storage.list_data_files(get_spec("raw.bars_daily"), root=root) == before  # untouched


def test_vacuum_respects_audit_window(root):
    bad = ingest.ingest_bars_daily(records=[daily_bar(D)], root=root)
    batches.invalidate_batch(bad.batch_id, "x", root=root)
    # default 90-day window: a just-invalidated batch is NOT a candidate yet
    assert compaction.vacuum("raw.bars_daily", confirm=False, root=root)["count"] == 0


def test_vacuum_confirm_deletes_dead_files_only(root):
    good = ingest.ingest_bars_daily(records=[daily_bar(D, close=1.0)], root=root)
    bad = ingest.ingest_bars_daily(records=[daily_bar(dt.date(2025, 11, 4), close=2.0)], root=root)
    batches.invalidate_batch(bad.batch_id, "x", root=root)
    res = compaction.vacuum("raw.bars_daily", older_than_days=0, confirm=True, root=root)
    assert res["count"] == 1
    # the good batch's file survives; the ledger row for the bad batch survives too
    remaining = storage.list_data_files(get_spec("raw.bars_daily"), root=root)
    assert len(remaining) == 1
    assert batches.get_batch(bad.batch_id, root=root) is not None


def test_snapshot_metadata_copies_ledgers(root):
    ingest.ingest_bars_daily(records=[daily_bar(D)], root=root)
    dest = compaction.snapshot_metadata(root=root)
    from pathlib import Path
    names = {p.name for p in Path(dest).glob("*.parquet")}
    assert {"batches.parquet", "sources.parquet", "field_registry.parquet"} <= names


def test_compact_merges_files_in_partition(root, reader):
    ingest.ingest_bars_daily(records=[daily_bar(D, symbol="A", close=1.0)], root=root)
    ingest.ingest_bars_daily(records=[daily_bar(D, symbol="B", close=2.0)], root=root)
    spec = get_spec("raw.bars_daily")
    assert len(storage.list_data_files(spec, root=root)) == 2
    plan = compaction.compact("raw.bars_daily", confirm=False, root=root)
    assert plan["partitions"] == 1
    compaction.compact("raw.bars_daily", confirm=True, root=root)
    assert len(storage.list_data_files(spec, root=root)) == 1
    # current view still correct after compaction
    got = {r["symbol"]: r["close"] for r in reader.read_bars_daily(["A", "B"], D, D).to_pylist()}
    assert got == {"A": 1.0, "B": 2.0}
