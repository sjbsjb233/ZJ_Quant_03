"""Bars API: adjustment (doc §6.3) and minute downsampling (doc §6.4)."""
from __future__ import annotations

import datetime as dt

import pytest

from quant_data import ingest

from .conftest import daily_bar, minute_bar


# --- adjustment -------------------------------------------------------------
def test_adjust_none_returns_raw(root, reader):
    ingest.ingest_bars_daily(records=[
        daily_bar(dt.date(2025, 11, 3), symbol="X", close=20.0, adj_factor=0.5),
        daily_bar(dt.date(2025, 11, 4), symbol="X", close=10.0, adj_factor=1.0),
    ], apply_mapping=False, root=root)
    assert reader.read_bars_daily(["X"], adjust="none").column("close").to_pylist() == [20.0, 10.0]


def test_adjust_pre_multiplies_by_factor(root, reader):
    ingest.ingest_bars_daily(records=[
        daily_bar(dt.date(2025, 11, 3), symbol="X", close=20.0, adj_factor=0.5),
        daily_bar(dt.date(2025, 11, 4), symbol="X", close=10.0, adj_factor=1.0),
    ], apply_mapping=False, root=root)
    # pre: price * adj_factor, latest stays unchanged
    assert reader.read_bars_daily(["X"], adjust="pre").column("close").to_pylist() == [10.0, 10.0]


def test_adjust_post_keeps_earliest_raw(root, reader):
    ingest.ingest_bars_daily(records=[
        daily_bar(dt.date(2025, 11, 3), symbol="X", close=20.0, adj_factor=0.5),
        daily_bar(dt.date(2025, 11, 4), symbol="X", close=10.0, adj_factor=1.0),
    ], apply_mapping=False, root=root)
    # post: normalised so the earliest row equals its raw price
    assert reader.read_bars_daily(["X"], adjust="post").column("close").to_pylist() == [20.0, 20.0]


def test_adjust_handles_null_factor_as_one(root, reader):
    ingest.ingest_bars_daily(records=[daily_bar(dt.date(2025, 11, 3), symbol="X", close=5.0,
                                                adj_factor=None)], apply_mapping=False, root=root)
    assert reader.read_bars_daily(["X"], adjust="pre").column("close").to_pylist() == [5.0]


# --- minute downsampling ----------------------------------------------------
def _ten_minutes(root):
    base = dt.datetime(2025, 11, 3, 9, 30)
    rows = [minute_bar(base + dt.timedelta(minutes=i), close=10 + i, volume=100) for i in range(10)]
    ingest.ingest_bars_minute(records=rows, apply_mapping=False, root=root)


def test_minute_default_is_1m(root, reader):
    _ten_minutes(root)
    assert reader.read_bars_minute(["000001.SZ"]).num_rows == 10


def test_downsample_5m_buckets(root, reader):
    _ten_minutes(root)
    t = reader.read_bars_minute(["000001.SZ"], frequency="5m").to_pylist()
    assert len(t) == 2
    first = t[0]
    assert first["open"] == 10.0  # first of bucket
    assert first["close"] == 14.0  # last of bucket
    assert first["volume"] == 500  # summed, as BIGINT
    assert first["frequency"] == "5m"


def test_downsample_aggregation_high_low(root, reader):
    _ten_minutes(root)
    t = reader.read_bars_minute(["000001.SZ"], frequency="5m").to_pylist()
    # closes 10..14 with high=close+0.05, low=close-0.05
    assert t[0]["high"] == pytest.approx(14.05)
    assert t[0]["low"] == pytest.approx(9.95)


def test_all_frequencies_supported(root, reader):
    _ten_minutes(root)
    for f in ("1m", "5m", "15m", "30m", "1h"):
        assert reader.read_bars_minute(["000001.SZ"], frequency=f).num_rows >= 1


def test_bad_frequency_rejected(root, reader):
    with pytest.raises(ValueError):
        reader.read_bars_minute(["000001.SZ"], frequency="3m")


def test_minute_adjust_after_downsample(root, reader):
    base = dt.datetime(2025, 11, 3, 9, 30)
    rows = [minute_bar(base + dt.timedelta(minutes=i), close=10.0, volume=100, adj_factor=0.5)
            for i in range(5)]
    ingest.ingest_bars_minute(records=rows, apply_mapping=False, root=root)
    t = reader.read_bars_minute(["000001.SZ"], frequency="5m", adjust="pre")
    assert t.column("close").to_pylist() == [5.0]  # 10 * 0.5
