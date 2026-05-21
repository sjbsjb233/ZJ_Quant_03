"""Cross-table mechanism tests (test plan §3): current view, multi-source,
PIT (incl. revision), adjustment."""
from __future__ import annotations

import datetime as dt

import pytest

from quant_data import ingest, storage
from quant_data.metadata import batches, sources
from quant_data.schemas import BARS_DAILY, BATCHES

from .conftest import daily_bar, fundamental, with_meta

pytestmark = pytest.mark.integration

D = dt.date(2024, 1, 2)


def _reg_and_write(root, batch_id, rows, source="akshare"):
    """Write a raw batch with explicit _ingested_at control (for tiebreaker tests)."""
    bid = batches.register_batch(source, "raw.bars_daily", batch_id=batch_id, root=root)
    storage.write_append(BARS_DAILY, rows, bid, root=root)
    batches.finalize_batch(bid, "success", row_count=len(rows), root=root)
    return bid


# --- §3.2 current view ------------------------------------------------------
def test_cv001_single_batch(root, reader):
    ingest.ingest_bars_daily(records=[daily_bar(D, symbol="600519.SH")],
                             apply_mapping=False, root=root)
    assert reader.read_bars_daily(["600519.SH"]).num_rows == 1


def test_cv002_all_invalidated_returns_empty(root, reader):
    r = ingest.ingest_bars_daily(records=[daily_bar(D, symbol="600519.SH")],
                                 apply_mapping=False, root=root)
    batches.invalidate_batch(r.batch_id, "x", root=root)
    assert reader.read_bars_daily(["600519.SH"]).num_rows == 0  # empty, not error


def test_cv003_newer_batch_wins(root, reader):
    ingest.ingest_bars_daily(records=[daily_bar(D, symbol="600519.SH", close=100.0)],
                             apply_mapping=False, root=root)
    ingest.ingest_bars_daily(records=[daily_bar(D, symbol="600519.SH", close=99.5)],
                             apply_mapping=False, root=root)
    assert reader.read_bars_daily(["600519.SH"], D, D).column("close").to_pylist() == [99.5]


def test_cv004_equal_ingested_at_tiebreaker_is_deterministic(root, reader):
    # same _ingested_at on both batches -> _batch_id DESC decides, deterministically
    t = dt.datetime(2024, 1, 2, 10, 0, 0)
    rows_a = with_meta([daily_bar(D, symbol="600519.SH", close=1.0)], "aaa")
    rows_z = with_meta([daily_bar(D, symbol="600519.SH", close=2.0)], "zzz")
    for r in rows_a + rows_z:
        r["_ingested_at"] = t
    _reg_and_write(root, "aaa", rows_a)
    _reg_and_write(root, "zzz", rows_z)
    got = reader.read_bars_daily(["600519.SH"], D, D).column("close").to_pylist()
    assert got == [2.0]  # 'zzz' > 'aaa'
    assert reader.read_bars_daily(["600519.SH"], D, D).column("close").to_pylist() == got  # stable


def test_cv005_corrupted_metadata_raises(root, reader):
    ingest.ingest_bars_daily(records=[daily_bar(D, symbol="600519.SH")],
                             apply_mapping=False, root=root)
    storage.single_file_path(BATCHES, root).write_bytes(b"not a parquet file")
    with pytest.raises(Exception):  # must NOT silently return unfiltered data
        reader.read_bars_daily(["600519.SH"])


# --- §3.3 multi-source ------------------------------------------------------
def test_mp002_higher_priority_wins(root, reader):
    sources.register_source("tushare_pro", priority=200, root=root)
    ingest.ingest_bars_daily(records=[daily_bar(D, symbol="600519.SH", close=100.0)],
                             source="akshare", apply_mapping=False, root=root)
    ingest.ingest_bars_daily(records=[daily_bar(D, symbol="600519.SH", close=99.0)],
                             source="tushare_pro", apply_mapping=False, root=root)
    assert reader.read_bars_daily(["600519.SH"], D, D).column("close").to_pylist() == [99.0]


def test_mp003_equal_priority_tiebreak(root, reader):
    sources.register_source("tushare_pro", priority=100, root=root)  # equal to akshare
    ingest.ingest_bars_daily(records=[daily_bar(D, symbol="600519.SH", close=100.0)],
                             source="akshare", apply_mapping=False, root=root)
    ingest.ingest_bars_daily(records=[daily_bar(D, symbol="600519.SH", close=99.0)],
                             source="tushare_pro", apply_mapping=False, root=root)
    # newer _ingested_at wins (tushare ingested second); deterministic
    out1 = reader.read_bars_daily(["600519.SH"], D, D).column("close").to_pylist()
    out2 = reader.read_bars_daily(["600519.SH"], D, D).column("close").to_pylist()
    assert out1 == out2 and out1 == [99.0]


def test_mp004_low_priority_fallback(root, reader):
    sources.register_source("tushare_pro", priority=50, root=root)
    # akshare (high) only has 600519; tushare (low) has 000001 too
    ingest.ingest_bars_daily(records=[daily_bar(D, symbol="600519.SH", close=100.0)],
                             source="akshare", apply_mapping=False, root=root)
    ingest.ingest_bars_daily(records=[daily_bar(D, symbol="000001.SZ", close=12.0)],
                             source="tushare_pro", apply_mapping=False, root=root)
    got = {r["symbol"]: r["close"]
           for r in reader.read_bars_daily(["600519.SH", "000001.SZ"], D, D).to_pylist()}
    assert got == {"600519.SH": 100.0, "000001.SZ": 12.0}  # fallback kept


def test_mp005_priority_switch_takes_effect_immediately(root, reader):
    sources.register_source("tushare_pro", priority=50, root=root)
    ingest.ingest_bars_daily(records=[daily_bar(D, symbol="600519.SH", close=100.0)],
                             source="akshare", apply_mapping=False, root=root)
    ingest.ingest_bars_daily(records=[daily_bar(D, symbol="600519.SH", close=99.0)],
                             source="tushare_pro", apply_mapping=False, root=root)
    assert reader.read_bars_daily(["600519.SH"], D, D).column("close").to_pylist() == [100.0]
    sources.update_source_priority("tushare_pro", 300, root=root)
    assert reader.read_bars_daily(["600519.SH"], D, D).column("close").to_pylist() == [99.0]


def test_mp006_inactive_source_excluded(root, reader):
    sources.register_source("tushare_pro", priority=50, root=root)
    ingest.ingest_bars_daily(records=[daily_bar(D, symbol="600519.SH", close=100.0)],
                             source="akshare", apply_mapping=False, root=root)
    ingest.ingest_bars_daily(records=[daily_bar(D, symbol="600519.SH", close=99.0)],
                             source="tushare_pro", apply_mapping=False, root=root)
    sources.update_source("akshare", is_active=False, root=root)
    assert reader.read_bars_daily(["600519.SH"], D, D).column("close").to_pylist() == [99.0]


# --- §3.4 PIT (incl. revision) ----------------------------------------------
def test_pt001_pit_boundary(root, reader):
    ingest.ingest_fundamentals(records=[fundamental(publish_date=dt.date(2024, 11, 1))], root=root)
    assert reader.read_fundamentals(["000001.SZ"], as_of_date=dt.date(2024, 10, 31)).num_rows == 0
    assert reader.read_fundamentals(["000001.SZ"], as_of_date=dt.date(2024, 11, 1)).num_rows == 1


def test_pt002_revision_strict_pit(root, reader):
    # original then revised disclosure of the SAME report period, both kept valid
    ingest.ingest_fundamentals(records=[fundamental(
        symbol="000001.SZ", report_period=dt.date(2024, 9, 30), period_type="Q3",
        publish_date=dt.date(2024, 11, 1), revenue=100e6)], root=root)
    ingest.ingest_fundamentals(records=[fundamental(
        symbol="000001.SZ", report_period=dt.date(2024, 9, 30), period_type="Q3",
        publish_date=dt.date(2024, 11, 15), revenue=102e6)], root=root)
    before = reader.read_fundamentals(["000001.SZ"], as_of_date=dt.date(2024, 11, 10))
    after = reader.read_fundamentals(["000001.SZ"], as_of_date=dt.date(2024, 11, 20))
    assert before.column("revenue").to_pylist() == [100e6]  # as-of original
    assert after.column("revenue").to_pylist() == [102e6]   # as-of revision


def test_pt002b_late_backfill_of_old_revision_does_not_leak(root, reader):
    # revision ingested, THEN the old version re-ingested later (latest _ingested_at).
    # publish_date-ordered dedup must still return the revision, not the stale value.
    ingest.ingest_fundamentals(records=[fundamental(
        symbol="000001.SZ", report_period=dt.date(2024, 9, 30), period_type="Q3",
        publish_date=dt.date(2024, 11, 15), revenue=102e6)], root=root)
    ingest.ingest_fundamentals(records=[fundamental(
        symbol="000001.SZ", report_period=dt.date(2024, 9, 30), period_type="Q3",
        publish_date=dt.date(2024, 11, 1), revenue=100e6)], root=root)  # backfill, ingested later
    after = reader.read_fundamentals(["000001.SZ"], as_of_date=dt.date(2024, 11, 20))
    assert after.column("revenue").to_pylist() == [102e6]


def test_pt003_as_of_required(root, reader):
    with pytest.raises(ValueError):
        reader.read_fundamentals(["000001.SZ"])


# --- §3.5 adjustment --------------------------------------------------------
def test_ad003_corporate_action_fix_propagates(root, reader):
    ingest.ingest_bars_daily(records=[
        daily_bar(dt.date(2024, 1, 2), symbol="600519.SH", close=200.0),
        daily_bar(dt.date(2024, 1, 3), symbol="600519.SH", close=190.0),
    ], apply_mapping=False, root=root)
    ca = ingest.ingest_corporate_actions(records=[{
        "symbol": "600519.SH", "action_date": dt.date(2024, 1, 3),
        "action_type": "cash_dividend", "cash_per_share": 10.0}],
        apply_mapping=False, root=root)
    f1 = reader.read_bars_daily(["600519.SH"], adjust="pre", recompute_adj=True
                                ).column("close").to_pylist()
    # fix the dividend: bump to 20/share via invalidate + reload
    batches.invalidate_batch(ca.batch_id, "wrong amount", root=root)
    ingest.ingest_corporate_actions(records=[{
        "symbol": "600519.SH", "action_date": dt.date(2024, 1, 3),
        "action_type": "cash_dividend", "cash_per_share": 20.0}],
        apply_mapping=False, root=root)
    f2 = reader.read_bars_daily(["600519.SH"], adjust="pre", recompute_adj=True
                                ).column("close").to_pylist()
    assert f1 != f2  # adjusted history changed because adj_factor is re-derived


def test_ad005_adjust_after_downsample(root, reader):
    base = dt.datetime(2024, 1, 2, 9, 30)
    rows = [{"datetime": base + dt.timedelta(minutes=i), "symbol": "600519.SH",
             "frequency": "1m", "open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0,
             "volume": 100, "amount": 1000.0, "adj_factor": 0.5} for i in range(5)]
    ingest.ingest_bars_minute(records=rows, apply_mapping=False, root=root)
    t = reader.read_bars_minute(["600519.SH"], frequency="5m", adjust="pre")
    # downsample first (high=10.1,low=9.9,close=10.0) THEN multiply by 0.5
    r = t.to_pylist()[0]
    assert r["close"] == 5.0 and r["high"] == pytest.approx(5.05) and r["low"] == pytest.approx(4.95)
