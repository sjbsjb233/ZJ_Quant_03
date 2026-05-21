"""Ingestion pipeline (doc §7.1)."""
from __future__ import annotations

import datetime as dt

import pytest

from quant_data import ingest, storage
from quant_data.metadata import batches, field_registry
from quant_data.schemas import get_spec

from .conftest import daily_bar


def test_field_mapping_renames_native_columns(root):
    native = [{"日期": dt.date(2025, 11, 3), "代码": "000001.SZ", "开盘": 10.0, "收盘": 10.2,
               "最高": 10.5, "最低": 9.9, "成交量": 100, "成交额": 1020.0}]
    res = ingest.ingest_bars_daily(records=native, source="akshare", root=root)
    assert res.row_count == 1
    # the written row should have standard names, not the Chinese source names
    import pyarrow.parquet as pq
    files = storage.list_data_files(get_spec("raw.bars_daily"), root=root)
    got = pq.read_table(files[0]).to_pylist()[0]
    assert got["close"] == 10.2 and got["symbol"] == "000001.SZ"


def test_non_core_fields_flow_into_extras(root):
    rec = [daily_bar(dt.date(2025, 11, 3))]
    rec[0]["roe"] = 15.3  # not a core column of bars_daily
    ingest.ingest_bars_daily(records=rec, source="akshare", apply_mapping=False, root=root)
    import pyarrow.parquet as pq
    f = storage.list_data_files(get_spec("raw.bars_daily"), root=root)[0]
    extras = dict(pq.read_table(f).to_pylist()[0]["_extras"])
    assert extras == {"roe": "15.3"}


def test_new_extras_field_auto_registered(root):
    rec = [daily_bar(dt.date(2025, 11, 3), _extras={"turnover_rate": "2.5"})]
    ingest.ingest_bars_daily(records=rec, apply_mapping=False, root=root)
    f = field_registry.get_field("raw.bars_daily", "turnover_rate", root=root)
    assert f["status"] == "extras" and f["data_type"] == "DOUBLE"


def test_metadata_columns_stamped(root):
    res = ingest.ingest_bars_daily(records=[daily_bar(dt.date(2025, 11, 3))], root=root)
    import pyarrow.parquet as pq
    f = storage.list_data_files(get_spec("raw.bars_daily"), root=root)[0]
    row = pq.read_table(f).to_pylist()[0]
    assert row["_batch_id"] == res.batch_id and row["_source"] == "akshare"
    assert row["_ingested_at"] is not None


def test_batch_lifecycle_recorded(root):
    res = ingest.ingest_bars_daily(records=[daily_bar(dt.date(2025, 11, 3))], root=root)
    b = batches.get_batch(res.batch_id, root=root)
    assert b["status"] == "success" and b["row_count"] == 1
    assert b["partition_keys"] == '["year=2025/month=11"]'


def test_failed_ingest_marks_batch_failed(root, monkeypatch):
    def boom(spec, data, batch_id, root=None):
        raise RuntimeError("disk full")
    monkeypatch.setattr(storage, "write_append", boom)
    with pytest.raises(RuntimeError):
        ingest.ingest_bars_daily(records=[daily_bar(dt.date(2025, 11, 3))], root=root)
    failed = batches.list_batches(target_table="raw.bars_daily", status="failed", root=root)
    assert len(failed) == 1 and "disk full" in failed[0]["error_message"]


def test_dedup_skips_identical_parameters(root):
    p = dict(symbols=["000001.SZ"], start="2025-11-03", end="2025-11-03")
    r1 = ingest.ingest_bars_daily(records=[daily_bar(dt.date(2025, 11, 3))], dedup=True, **p, root=root)
    r2 = ingest.ingest_bars_daily(records=[daily_bar(dt.date(2025, 11, 3))], dedup=True, **p, root=root)
    assert r1.skipped is False and r2.skipped is True
    assert len(storage.list_data_files(get_spec("raw.bars_daily"), root=root)) == 1


def test_quality_issues_recorded(root):
    bad = [daily_bar(dt.date(2025, 11, 3), high=1.0, low=100.0)]  # high < low
    res = ingest.ingest_bars_daily(records=bad, root=root)
    assert any(i.check == "high_lt_low" for i in res.issues)
    b = batches.get_batch(res.batch_id, root=root)
    assert "quality_issues" in (b["notes"] or "")  # non-blocking, but recorded


def test_fetch_fn_path(root):
    def fetch(symbols, start, end):
        return [daily_bar(dt.date(2025, 11, 3), symbol=s) for s in symbols]
    res = ingest.ingest_bars_daily(fetch_fn=fetch, symbols=["A", "B"], start="2025-11-03",
                                   end="2025-11-03", apply_mapping=False, root=root)
    assert res.row_count == 2


def test_ingest_rejects_reference_table(root):
    with pytest.raises(ValueError):
        ingest.ingest("reference.symbols", [], root=root)
