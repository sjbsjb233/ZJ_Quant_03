"""Current-view semantics (doc §6.1, §1.3, §7.2)."""
from __future__ import annotations

import datetime as dt

import pytest

from quant_data import ColumnNotInViewError, ingest
from quant_data.metadata import batches, sources
from quant_data.schemas import get_spec

from .conftest import daily_bar

D = dt.date(2025, 11, 3)


def _close(reader, root):
    t = reader.read_bars_daily(["000001.SZ"], D, D)
    return t.column("close").to_pylist()


def test_latest_valid_batch_wins_for_same_key(root, reader):
    ingest.ingest_bars_daily(records=[daily_bar(D, close=10.0)], root=root)
    ingest.ingest_bars_daily(records=[daily_bar(D, close=11.0)], root=root)
    assert _close(reader, root) == [11.0]  # newest batch by _ingested_at


def test_invalidated_batch_disappears_from_view(root, reader):
    ingest.ingest_bars_daily(records=[daily_bar(D, close=10.0)], root=root)
    bad = ingest.ingest_bars_daily(records=[daily_bar(D, close=999.0)], root=root)
    assert _close(reader, root) == [999.0]
    batches.invalidate_batch(bad.batch_id, "wrong", root=root)
    assert _close(reader, root) == [10.0]  # reverts to prior good batch


def test_invalidated_rows_remain_on_disk(root, reader):
    bad = ingest.ingest_bars_daily(records=[daily_bar(D, close=999.0)], root=root)
    batches.invalidate_batch(bad.batch_id, "wrong", root=root)
    # current view hides it, but the physical rows survive for audit (doc §1.3)
    from quant_data import storage
    from quant_data.schemas import get_spec
    import pyarrow.parquet as pq
    files = storage.list_data_files(get_spec("raw.bars_daily"), root=root)
    on_disk = [r for f in files for r in pq.read_table(f).to_pylist()]
    assert any(r["close"] == 999.0 for r in on_disk)


def test_source_priority_resolves_conflict(root, reader):
    sources.register_source("tushare_pro", priority=50, root=root)  # below akshare(100)
    ingest.ingest_bars_daily(records=[daily_bar(D, close=10.0)], source="akshare", root=root)
    ingest.ingest_bars_daily(records=[daily_bar(D, close=20.0)], source="tushare_pro", root=root)
    assert _close(reader, root) == [10.0]  # higher priority akshare wins
    sources.update_source_priority("tushare_pro", 200, root=root)
    assert _close(reader, root) == [20.0]  # priority flip changes the winner


def test_inactive_source_excluded(root, reader):
    ingest.ingest_bars_daily(records=[daily_bar(D, close=10.0)], source="akshare", root=root)
    sources.update_source("akshare", is_active=False, root=root)
    assert _close(reader, root) == []  # no active source -> nothing in view


def test_empty_when_no_data(root, reader):
    t = reader.read_bars_daily(["000001.SZ"], D, D)
    assert t.num_rows == 0
    assert "close" in t.column_names  # typed empty table


def test_predicate_pushdown_filters(root, reader):
    ingest.ingest_bars_daily(records=[
        daily_bar(dt.date(2025, 11, 3), symbol="A", close=1.0),
        daily_bar(dt.date(2025, 11, 4), symbol="A", close=2.0),
        daily_bar(dt.date(2025, 11, 3), symbol="B", close=3.0),
    ], apply_mapping=False, root=root)
    t = reader.read_bars_daily(["A"], dt.date(2025, 11, 3), dt.date(2025, 11, 3))
    assert t.column("close").to_pylist() == [1.0]


# --- latest_date_in_view: cheap MAX(date) without the de-dup window (P0 fix) ---
def test_latest_date_in_view_matches_current_view(root, reader):
    """The cheap path must equal the (expensive) current-view MAX(date)."""
    from quant_data.schemas import get_spec
    d1, d2 = dt.date(2025, 11, 3), dt.date(2025, 11, 4)
    ingest.ingest_bars_daily(records=[daily_bar(d1, close=10.0)], root=root)
    ingest.ingest_bars_daily(records=[daily_bar(d2, close=11.0)], root=root)
    spec = get_spec("raw.bars_daily")
    old = reader.aggregate_current_view(spec, "MAX(date) AS d", "").fetchall()[0][0]
    new = reader.latest_date_in_view("raw.bars_daily")
    assert new == d2 and str(old)[:10] == new.isoformat()


def test_latest_date_in_view_excludes_invalidated_latest(root, reader):
    """If the latest date's only rows are invalidated, latest must revert -- the
    de-dup-free path still respects valid-batch filtering."""
    from quant_data.schemas import get_spec
    d1, d2 = dt.date(2025, 11, 3), dt.date(2025, 11, 4)
    ingest.ingest_bars_daily(records=[daily_bar(d1, close=10.0)], root=root)
    bad = ingest.ingest_bars_daily(records=[daily_bar(d2, close=11.0)], root=root)
    assert reader.latest_date_in_view("raw.bars_daily") == d2
    batches.invalidate_batch(bad.batch_id, "wrong", root=root)
    spec = get_spec("raw.bars_daily")
    old = reader.aggregate_current_view(spec, "MAX(date) AS d", "").fetchall()[0][0]
    new = reader.latest_date_in_view("raw.bars_daily")
    assert new == d1 and str(old)[:10] == new.isoformat()


def test_latest_date_in_view_on_or_before(root, reader):
    d1, d2 = dt.date(2025, 11, 3), dt.date(2025, 11, 10)
    ingest.ingest_bars_daily(records=[daily_bar(d1, close=10.0)], root=root)
    ingest.ingest_bars_daily(records=[daily_bar(d2, close=11.0)], root=root)
    assert reader.latest_date_in_view("raw.bars_daily", on_or_before=dt.date(2025, 11, 5)) == d1
    assert reader.latest_date_in_view("raw.bars_daily", on_or_before=dt.date(2000, 1, 1)) is None


def test_aggregate_missing_column_raises_typed_error(root, reader):
    """A projection referencing a column the parquet data lacks (e.g. market-cap
    on legacy bars) surfaces ColumnNotInViewError -- not a raw BinderException --
    so the API can degrade gracefully instead of 500ing."""
    ingest.ingest_bars_daily(records=[daily_bar(D, close=10.0)], root=root)
    spec = get_spec("raw.bars_daily")
    with pytest.raises(ColumnNotInViewError) as ei:
        reader.aggregate_current_view(
            spec, "symbol, last(nonexistent_col ORDER BY date) AS v", "GROUP BY symbol")
    assert ei.value.column == "nonexistent_col"
    assert ei.value.table == "raw.bars_daily"


def test_aggregate_existing_column_does_not_raise(root, reader):
    """Sanity: a present column still aggregates normally (no over-eager catch)."""
    ingest.ingest_bars_daily(records=[daily_bar(D, close=10.0)], root=root)
    spec = get_spec("raw.bars_daily")
    rows = reader.aggregate_current_view(
        spec, "symbol, last(close ORDER BY date) AS v", "GROUP BY symbol").fetchall()
    assert rows == [("000001.SZ", 10.0)]
