"""Schemas (doc §1-§5) + low-level storage behaviour."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pyarrow as pa
import pytest

from quant_data import schemas, storage
from quant_data.schemas import get_spec

from .conftest import daily_bar, with_meta


def test_every_documented_table_is_registered():
    expected = {
        "raw.bars_daily", "raw.bars_minute", "raw.fundamentals", "raw.universe",
        "raw.calendar", "raw.corporate_actions", "reference.symbols",
        "reference.sectors", "metadata.batches", "metadata.sources",
        "metadata.field_registry",
    }
    assert expected <= set(schemas.ALL_TABLES)


def test_raw_tables_carry_the_four_metadata_columns():
    # doc §1.1: every raw row has _batch_id/_source/_ingested_at/_extras
    for spec in schemas.RAW_TABLES.values():
        assert set(spec.meta_names()) == {"_batch_id", "_source", "_ingested_at", "_extras"}


def test_reference_meta_columns():
    # symbols has _extras; sectors does not (doc §5.1/§5.2)
    assert schemas.SYMBOLS.has_extras()
    assert not schemas.SECTORS.has_extras()
    assert "_updated_at" in schemas.SECTORS.meta_names()


def test_bars_daily_dropped_v01_columns():
    # doc §3.1: no 'frequency'/'datetime' on the daily table
    names = schemas.BARS_DAILY.business_names()
    assert "frequency" not in names and "datetime" not in names
    assert "date" in names


def test_partition_specs_match_doc():
    assert schemas.BARS_DAILY.partition_names() == ["year", "month"]
    assert schemas.BARS_MINUTE.partition_names() == ["year", "month"]
    assert schemas.FUNDAMENTALS.partition_names() == ["year", "quarter"]
    assert schemas.UNIVERSE.partition_names() == ["year"]
    assert schemas.CALENDAR.partition_names() == []


def test_normalize_fills_missing_and_builds_map():
    spec = get_spec("raw.bars_daily")
    tbl = storage.normalize_to_arrow(spec, [{"date": dt.date(2025, 1, 2), "symbol": "X",
                                             "close": 1.0, "_extras": {"k": "v"}}])
    assert tbl.schema.equals(spec.arrow_schema())
    assert tbl.column("vwap").null_count == 1  # missing -> null
    assert tbl.column("_extras").to_pylist() == [[("k", "v")]]


def test_append_only_writes_new_files_per_batch(root):
    spec = get_spec("raw.bars_daily")
    storage.write_append(spec, with_meta([daily_bar(dt.date(2025, 11, 3))], "batch-A"), "batch-A", root=root)
    storage.write_append(spec, with_meta([daily_bar(dt.date(2025, 11, 3))], "batch-B"), "batch-B", root=root)
    files = storage.list_data_files(spec, root=root)
    assert len(files) == 2  # same key, two immutable files
    assert all("year=2025/month=11" in f for f in files)


def test_partition_key_strings(root):
    spec = get_spec("raw.bars_daily")
    rows = with_meta([daily_bar(dt.date(2025, 11, 3)), daily_bar(dt.date(2025, 12, 1))])
    parts = storage.write_append(spec, rows, "b1", root=root)
    assert set(parts) == {"year=2025/month=11", "year=2025/month=12"}


def test_batch_id_is_not_a_partition_column(root):
    # doc §1.4: _batch_id is a plain column, never a partition key
    spec = get_spec("raw.bars_daily")
    storage.write_append(spec, with_meta([daily_bar(dt.date(2025, 11, 3))]), "b1", root=root)
    f = storage.list_data_files(spec, root=root)[0]
    assert "_batch_id=" not in f


def test_unpartitioned_write_one_file_per_batch(root):
    spec = get_spec("raw.calendar")
    rows = with_meta([{"date": dt.date(2025, 11, 3), "market": "CN-A", "is_trading_day": True,
                       "month_end_trading_day": False, "quarter_end_trading_day": False,
                       "year_end_trading_day": False}])
    storage.write_append(spec, rows, "cal-1", root=root)
    files = storage.list_data_files(spec, root=root)
    assert len(files) == 1 and files[0].endswith("cal-1.parquet")


def test_single_file_roundtrip_with_extras(root):
    spec = get_spec("reference.symbols")
    rows = [{n: None for n in spec.all_names()}]
    rows[0].update(symbol="X", name="n", exchange="SZ", currency="CNY", is_active=True,
                   _extras={"isin": "abc"}, _updated_at=dt.datetime(2025, 1, 1), _source="manual")
    storage.write_rows(spec, rows, root=root)
    back = storage.read_rows(spec, root=root)
    assert back[0]["_extras"] == {"isin": "abc"}


def test_empty_read_returns_typed_empty_table(root):
    spec = get_spec("raw.bars_daily")
    assert storage.empty_arrow(spec).num_rows == 0


def test_write_append_rejects_mutable_table(root):
    with pytest.raises(ValueError):
        storage.write_append(get_spec("metadata.batches"), [], "b", root=root)


def test_layout_includes_derived_placeholders(root):
    # doc §4: derived/ namespace reserved this period
    base = Path(root)
    for d in ("derived/factors_daily", "derived/signals_daily", "derived/factor_metrics"):
        assert (base / d).is_dir()
