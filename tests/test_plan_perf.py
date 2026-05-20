"""Performance baselines (test plan §6).

Marked @perf so they're DESELECTED by default (run with `pytest -m perf`).
Thresholds are deliberately generous -- these guard against pathological
regressions (e.g. losing partition pruning), not exact machine-specific latency.
"""
from __future__ import annotations

import datetime as dt
import time

import pytest

from quant_data import DataReader, ingest
from quant_data.metadata import batches

from ._world import gen_bars_daily, gen_minute, trading_days

pytestmark = pytest.mark.perf


def _elapsed(fn):
    t0 = time.perf_counter()
    out = fn()
    return out, time.perf_counter() - t0


def test_perf_read_bars_daily_year(root):
    syms = [f"{600000 + i}.SH" for i in range(100)]
    days = trading_days(dt.date(2024, 1, 1), dt.date(2024, 12, 31))
    ingest.ingest_bars_daily(records=gen_bars_daily(syms, days), apply_mapping=False, root=root)
    rd = DataReader(root=root)
    out, dt_s = _elapsed(lambda: rd.read_bars_daily(syms, dt.date(2024, 1, 1), dt.date(2024, 12, 31)))
    assert out.num_rows == len(syms) * len(days)
    assert dt_s < 10.0, f"read_bars_daily year took {dt_s:.2f}s"


def test_perf_minute_downsample(root):
    rows = []
    for d in trading_days(dt.date(2024, 1, 1), dt.date(2024, 1, 31)):
        rows += gen_minute("600519.SH", d)
    ingest.ingest_bars_minute(records=rows, apply_mapping=False, root=root)
    rd = DataReader(root=root)
    out, dt_s = _elapsed(lambda: rd.read_bars_minute(["600519.SH"], frequency="5m"))
    assert out.num_rows > 0
    assert dt_s < 10.0, f"minute 5m downsample took {dt_s:.2f}s"


def test_perf_many_invalidated_batches_no_blowup(root):
    # §6.3: a pile of invalidated batches must not tank the current view
    good = ingest.ingest_bars_daily(records=gen_bars_daily(["600519.SH"],
                                    trading_days(dt.date(2024, 1, 1), dt.date(2024, 3, 31))),
                                    apply_mapping=False, root=root)
    assert good.row_count > 0
    for _ in range(500):
        b = batches.register_batch("akshare", "raw.bars_daily", root=root)
        batches.finalize_batch(b, "success", row_count=0, root=root)
        batches.invalidate_batch(b, "noise", root=root)
    rd = DataReader(root=root)
    out, dt_s = _elapsed(lambda: rd.read_bars_daily(["600519.SH"]))
    assert out.num_rows > 0
    assert dt_s < 10.0, f"read with 500 invalidated batches took {dt_s:.2f}s"
