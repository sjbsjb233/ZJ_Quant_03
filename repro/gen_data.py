"""Generate production-scale raw data on disk to reproduce the OOM audit.

Targets (per the audit report):
  raw.bars_daily   ~18.6M rows  (5000 symbols x ~3700 trading days)
  raw.universe     ~15.4M rows
  raw.corporate_actions  a few per symbol

Writes are vectorised with numpy and streamed batch-by-batch so the *generator*
never holds the whole dataset in memory -- the point is to test the *readers*,
not to OOM during generation.

A second source ("tushare") is layered onto a slice of bars_daily so
governance.diff_sources has two active sources to compare.
"""
from __future__ import annotations

import datetime as dt
import sys
import time

import numpy as np
import pyarrow as pa

import quant_data as q
from quant_data import storage
from quant_data.metadata import batches, sources
from quant_data.schemas import get_spec

ROOT = sys.argv[1] if len(sys.argv) > 1 else "/home/user/ZJ_Quant_03/repro/data"

N_SYMBOLS = int(sys.argv[2]) if len(sys.argv) > 2 else 5000
N_DAYS = int(sys.argv[3]) if len(sys.argv) > 3 else 3700   # ~15 trading years
SYM_PER_BATCH = 250

EPOCH = dt.date(2005, 1, 3)


def trading_dates(n: int) -> list[dt.date]:
    out, d = [], EPOCH
    while len(out) < n:
        if d.weekday() < 5:  # weekday approximation of a trading calendar
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def symbols(n: int) -> list[str]:
    # synthetic but well-formed A-share-ish codes
    out = []
    for i in range(n):
        ex = ".SH" if i % 2 == 0 else ".SZ"
        out.append(f"{600000 + i:06d}{ex}")
    return out


def write_batch(spec, cols: dict, source: str, root: str) -> None:
    bid = batches.register_batch(source, spec.name, root=root)
    nrows = len(cols["symbol"])
    cols["_batch_id"] = pa.array([bid] * nrows, pa.string())
    tbl = pa.table(cols)
    parts = storage.write_append(spec, tbl, bid, root=root)
    batches.finalize_batch(bid, "success", row_count=tbl.num_rows,
                           partition_keys=parts, root=root)


def gen_bars(root: str, syms: list[str], days: list[dt.date], second_source_slice: int):
    spec = get_spec("raw.bars_daily")
    day_arr = np.array(days, dtype="datetime64[D]")
    ndays = len(days)
    ingested = np.datetime64("2025-01-01T00:00:00", "us")
    total = 0
    t0 = time.time()
    for start in range(0, len(syms), SYM_PER_BATCH):
        chunk = syms[start:start + SYM_PER_BATCH]
        nrows = len(chunk) * ndays
        sym_col = np.repeat(np.array(chunk, dtype=object), ndays)
        date_col = np.tile(day_arr, len(chunk))
        rng = np.random.default_rng(start)
        close = 10.0 + rng.random(nrows) * 90.0
        cols = {
            "date": pa.array(date_col, type=pa.date32()),
            "symbol": pa.array(sym_col, type=pa.string()),
            "open": pa.array(close, pa.float64()),
            "high": pa.array(close + 1.0, pa.float64()),
            "low": pa.array(close - 1.0, pa.float64()),
            "close": pa.array(close, pa.float64()),
            "pre_close": pa.array(close, pa.float64()),
            "volume": pa.array(rng.integers(10000, 500000, nrows), pa.int64()),
            "amount": pa.array(close * 1000.0, pa.float64()),
            "vwap": pa.array(close, pa.float64()),
            "adj_factor": pa.array(np.ones(nrows), pa.float64()),
            "_batch_id": pa.array(["x"] * nrows, pa.string()),  # overwritten below
            "_source": pa.array(["akshare"] * nrows, pa.string()),
            "_ingested_at": pa.array(np.full(nrows, ingested), pa.timestamp("us")),
        }
        write_batch(spec, cols, "akshare", root)
        total += nrows
        if (start // SYM_PER_BATCH) % 4 == 0:
            print(f"  bars_daily akshare: {total:,} rows  ({time.time()-t0:.0f}s)", flush=True)

    # second source on a slice of symbols -> two active sources for diff_sources
    sl = syms[:second_source_slice]
    for start in range(0, len(sl), SYM_PER_BATCH):
        chunk = sl[start:start + SYM_PER_BATCH]
        nrows = len(chunk) * ndays
        sym_col = np.repeat(np.array(chunk, dtype=object), ndays)
        date_col = np.tile(day_arr, len(chunk))
        rng = np.random.default_rng(99000 + start)
        close = 10.0 + rng.random(nrows) * 90.0
        cols = {
            "date": pa.array(date_col, type=pa.date32()),
            "symbol": pa.array(sym_col, type=pa.string()),
            "open": pa.array(close, pa.float64()),
            "high": pa.array(close + 1.0, pa.float64()),
            "low": pa.array(close - 1.0, pa.float64()),
            "close": pa.array(close + 0.01, pa.float64()),  # slight divergence
            "pre_close": pa.array(close, pa.float64()),
            "volume": pa.array(rng.integers(10000, 500000, nrows), pa.int64()),
            "amount": pa.array(close * 1000.0, pa.float64()),
            "vwap": pa.array(close, pa.float64()),
            "adj_factor": pa.array(np.ones(nrows), pa.float64()),
            "_batch_id": pa.array(["x"] * nrows, pa.string()),
            "_source": pa.array(["tushare"] * nrows, pa.string()),
            "_ingested_at": pa.array(np.full(nrows, ingested), pa.timestamp("us")),
        }
        write_batch(spec, cols, "tushare", root)
    print(f"  bars_daily DONE: {total:,} akshare rows + slice tushare ({time.time()-t0:.0f}s)", flush=True)


def gen_universe(root: str, syms: list[str], days: list[dt.date]):
    spec = get_spec("raw.universe")
    day_arr = np.array(days, dtype="datetime64[D]")
    ndays = len(days)
    ingested = np.datetime64("2025-01-01T00:00:00", "us")
    total, t0 = 0, time.time()
    for start in range(0, len(syms), SYM_PER_BATCH):
        chunk = syms[start:start + SYM_PER_BATCH]
        nrows = len(chunk) * ndays
        sym_col = np.repeat(np.array(chunk, dtype=object), ndays)
        date_col = np.tile(day_arr, len(chunk))
        cols = {
            "date": pa.array(date_col, type=pa.date32()),
            "symbol": pa.array(sym_col, type=pa.string()),
            "is_tradable": pa.array(np.ones(nrows, bool), pa.bool_()),
            "is_listed": pa.array(np.ones(nrows, bool), pa.bool_()),
            "is_suspended": pa.array(np.zeros(nrows, bool), pa.bool_()),
            "st_status": pa.array(["normal"] * nrows, pa.string()),
            "in_hs300": pa.array(np.zeros(nrows, bool), pa.bool_()),
            "in_zz500": pa.array(np.zeros(nrows, bool), pa.bool_()),
            "in_zz1000": pa.array(np.zeros(nrows, bool), pa.bool_()),
            "in_sse50": pa.array(np.zeros(nrows, bool), pa.bool_()),
            "limit_up_status": pa.array(["normal"] * nrows, pa.string()),
            "listed_days": pa.array(np.full(nrows, 1000, np.int32), pa.int32()),
            "_batch_id": pa.array(["x"] * nrows, pa.string()),
            "_source": pa.array(["akshare"] * nrows, pa.string()),
            "_ingested_at": pa.array(np.full(nrows, ingested), pa.timestamp("us")),
        }
        write_batch(spec, cols, "akshare", root)
        total += nrows
    print(f"  universe DONE: {total:,} rows ({time.time()-t0:.0f}s)", flush=True)


def gen_corp(root: str, syms: list[str]):
    spec = get_spec("raw.corporate_actions")
    ingested = np.datetime64("2025-01-01T00:00:00", "us")
    # ~3 actions per symbol
    rows_sym, adate, atype, cash = [], [], [], []
    base = dt.date(2010, 6, 1)
    for i, s in enumerate(syms):
        for k in range(3):
            rows_sym.append(s)
            adate.append(base + dt.timedelta(days=365 * k + (i % 200)))
            atype.append("cash_dividend")
            cash.append(0.5)
    n = len(rows_sym)
    cols = {
        "symbol": pa.array(rows_sym, pa.string()),
        "action_date": pa.array(np.array(adate, dtype="datetime64[D]"), pa.date32()),
        "announce_date": pa.array(np.array(adate, dtype="datetime64[D]"), pa.date32()),
        "action_type": pa.array(atype, pa.string()),
        "cash_per_share": pa.array(cash, pa.float64()),
        "_batch_id": pa.array(["x"] * n, pa.string()),
        "_source": pa.array(["akshare"] * n, pa.string()),
        "_ingested_at": pa.array(np.full(n, ingested), pa.timestamp("us")),
    }
    write_batch(spec, cols, "akshare", root)
    print(f"  corporate_actions DONE: {n:,} rows", flush=True)


def main():
    print(f"bootstrap root={ROOT}", flush=True)
    q.bootstrap(ROOT)
    if sources.get_source("tushare", root=ROOT) is None:
        sources.register_source("tushare", display_name="tushare", category="external_api",
                                priority=90, auth_method="none", root=ROOT)
    syms = symbols(N_SYMBOLS)
    days = trading_dates(N_DAYS)
    print(f"symbols={len(syms)} days={len(days)} -> bars_daily target={len(syms)*len(days):,}", flush=True)
    gen_bars(ROOT, syms, days, second_source_slice=500)
    gen_universe(ROOT, syms, days)
    gen_corp(ROOT, syms)
    print("ALL DONE", flush=True)


if __name__ == "__main__":
    main()
