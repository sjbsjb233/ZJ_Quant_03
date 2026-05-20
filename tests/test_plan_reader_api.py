"""DataReader API tests (test plan §4): read_bars_daily / read_bars_minute
(+ downsampling correctness), read_fundamentals, universe/calendar/symbols."""
from __future__ import annotations

import datetime as dt

import pytest

from quant_data import ingest

from .conftest import daily_bar

pytestmark = pytest.mark.unit

DAY = dt.date(2024, 1, 2)


def _minute(ts, **kw):
    base = dict(symbol="600519.SH", frequency="1m", open=100.0, high=100.0, low=100.0,
                close=100.0, volume=100, amount=10000.0, adj_factor=1.0)
    base.update(datetime=ts, **kw)
    return base


# --- §4.1 read_bars_daily ---------------------------------------------------
def test_rd001_single_symbol_single_day(standard_world, reader):
    t = reader.read_bars_daily(["600519.SH"], DAY, DAY)
    assert t.num_rows == 1


def test_rd004_empty_symbol_list_returns_empty(standard_world, reader):
    # empty list means "no symbols", not "whole market"
    assert reader.read_bars_daily([], DAY, DAY).num_rows == 0


def test_rd005_start_after_end_raises(standard_world, reader):
    with pytest.raises(ValueError):
        reader.read_bars_daily(["600519.SH"], dt.date(2024, 3, 1), dt.date(2024, 1, 1))


def test_rd007_invalid_adjust_raises(standard_world, reader):
    with pytest.raises(ValueError):
        reader.read_bars_daily(["600519.SH"], adjust="weird")


def test_rd008_cross_year_partitions(root, reader):
    ingest.ingest_bars_daily(records=[
        daily_bar(dt.date(2024, 12, 31), symbol="600519.SH", close=1.0),
        daily_bar(dt.date(2025, 1, 2), symbol="600519.SH", close=2.0),
    ], apply_mapping=False, root=root)
    t = reader.read_bars_daily(["600519.SH"], dt.date(2024, 12, 1), dt.date(2025, 1, 31))
    assert t.column("close").to_pylist() == [1.0, 2.0]  # both year partitions read


def test_rd009_nonexistent_symbol_returns_empty(standard_world, reader):
    assert reader.read_bars_daily(["999999.SH"], DAY, DAY).num_rows == 0


def test_rd010_column_order_stable(standard_world, reader):
    a = reader.read_bars_daily(["600519.SH"], DAY, DAY).column_names
    b = reader.read_bars_daily(["000001.SZ"], DAY, DAY).column_names
    assert a == b


# --- §4.2 read_bars_minute + downsampling -----------------------------------
def test_rm002_full_day_5m_has_48_buckets(root, reader):
    from ._world import gen_minute
    ingest.ingest_bars_minute(records=gen_minute("600519.SH", DAY), apply_mapping=False, root=root)
    assert reader.read_bars_minute(["600519.SH"], frequency="5m").num_rows == 48


def test_rm003_aggregation_values_exact(root, reader):
    base = dt.datetime(2024, 1, 2, 9, 30)
    rows = [
        _minute(base + dt.timedelta(minutes=0), open=100, high=101, low=99, close=100.5, volume=1000, amount=100000),
        _minute(base + dt.timedelta(minutes=1), open=100.5, high=102, low=100, close=101, volume=1500, amount=151500),
        _minute(base + dt.timedelta(minutes=2), open=101, high=101.5, low=100.5, close=100.5, volume=800, amount=80800),
        _minute(base + dt.timedelta(minutes=3), open=100.5, high=100.8, low=100.2, close=100.7, volume=500, amount=50350),
        _minute(base + dt.timedelta(minutes=4), open=100.7, high=101.2, low=100.7, close=101.0, volume=1200, amount=121200),
    ]
    ingest.ingest_bars_minute(records=rows, apply_mapping=False, root=root)
    bar = reader.read_bars_minute(["600519.SH"], frequency="5m").to_pylist()[0]
    assert bar["open"] == 100        # FIRST
    assert bar["high"] == 102        # MAX
    assert bar["low"] == 99          # MIN
    assert bar["close"] == 101.0     # LAST
    assert bar["volume"] == 5000     # SUM
    assert bar["amount"] == pytest.approx(503850)  # SUM
    assert bar["vwap"] == pytest.approx(503850 / 5000)  # recomputed, not last(vwap)


def test_rm004_cross_lunch_1h_no_empty_bucket(root, reader):
    rows = []
    for h, m0 in ((10, 30), (13, 0)):
        start = dt.datetime(2024, 1, 2, h, m0)
        rows += [_minute(start + dt.timedelta(minutes=i)) for i in range(30)]
    ingest.ingest_bars_minute(records=rows, apply_mapping=False, root=root)
    buckets = {r["datetime"].hour for r in
               reader.read_bars_minute(["600519.SH"], frequency="1h").to_pylist()}
    assert 12 not in buckets  # lunch break produces no phantom 12:00 bucket
    assert {10, 13} <= buckets


def test_rm006_invalid_frequency_raises(root, reader):
    with pytest.raises(ValueError):
        reader.read_bars_minute(["600519.SH"], frequency="3m")


def test_rm007_missing_minutes_still_aggregate(root, reader):
    # only 3 of the 5 minutes present in a bucket -> still aggregates those 3
    base = dt.datetime(2024, 1, 2, 9, 30)
    rows = [_minute(base, close=10, volume=100),
            _minute(base + dt.timedelta(minutes=2), close=11, volume=100),
            _minute(base + dt.timedelta(minutes=4), close=12, volume=100)]
    ingest.ingest_bars_minute(records=rows, apply_mapping=False, root=root)
    bar = reader.read_bars_minute(["600519.SH"], frequency="5m").to_pylist()[0]
    assert bar["close"] == 12 and bar["volume"] == 300


def test_rm009_cross_day_not_merged(root, reader):
    rows = [_minute(dt.datetime(2024, 1, 2, 14, 59), close=10),
            _minute(dt.datetime(2024, 1, 3, 9, 30), close=20)]
    ingest.ingest_bars_minute(records=rows, apply_mapping=False, root=root)
    out = reader.read_bars_minute(["600519.SH"], frequency="1h").to_pylist()
    days = {r["datetime"].date() for r in out}
    assert days == {dt.date(2024, 1, 2), dt.date(2024, 1, 3)}  # two distinct buckets


# --- §4.3/§4.4 other reads --------------------------------------------------
def test_rf005_multiple_period_types_coexist(root, reader):
    ingest.ingest_fundamentals(records=[
        {"symbol": "600519.SH", "report_period": dt.date(2024, 6, 30), "period_type": "Q2",
         "publish_date": dt.date(2024, 8, 28), "currency": "CNY", "revenue": 1.0},
        {"symbol": "600519.SH", "report_period": dt.date(2024, 6, 30), "period_type": "semi",
         "publish_date": dt.date(2024, 8, 28), "currency": "CNY", "revenue": 2.0},
    ], root=root)
    t = reader.read_fundamentals(["600519.SH"], as_of_date=dt.date(2024, 9, 1))
    assert t.num_rows == 2  # Q2 and semi are distinct PKs, not deduped


def test_ru002_universe_history_preserved(standard_world, reader):
    # in_hs300 must reflect the queried date, not the latest state
    root = standard_world["root"]
    ingest.ingest_universe(records=[{
        "date": dt.date(2024, 2, 1), "symbol": "600519.SH", "is_tradable": True,
        "is_listed": True, "is_suspended": False, "st_status": "normal", "in_hs300": False,
        "in_zz500": False, "in_zz1000": False, "in_sse50": True}],
        apply_mapping=False, root=root)
    jan = reader.read_universe(["600519.SH"], dt.date(2024, 1, 2), dt.date(2024, 1, 2))
    feb = reader.read_universe(["600519.SH"], dt.date(2024, 2, 1), dt.date(2024, 2, 1))
    assert jan.column("in_hs300").to_pylist() == [True]
    assert feb.column("in_hs300").to_pylist() == [False]


def test_rc002_calendar_prev_next_across_holiday(standard_world, reader):
    # 2024-02-08 is the last trading day before Spring Festival; next is 2024-02-19
    t = reader.read_calendar("CN-A", dt.date(2024, 2, 8), dt.date(2024, 2, 8)).to_pylist()[0]
    assert t["next_trading_day"] == dt.date(2024, 2, 19)


def test_rs001_read_symbols_active_filter(standard_world, reader):
    # 600145.SH is delisted/inactive in the standard world
    active = set(reader.read_symbols(active_only=True).column("symbol").to_pylist())
    allsym = set(reader.read_symbols().column("symbol").to_pylist())
    assert "600145.SH" in allsym and "600145.SH" not in active
