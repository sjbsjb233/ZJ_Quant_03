"""Concurrency / stress tests for the data layer (test plan §CC extension).

Always-on tests keep volumes small enough for CI; the heavy throughput run is
marked @perf (deselected by default; run with `pytest -m perf`).

Invariants exercised:
  * CC-S1  parallel disjoint ingests all become visible, with exact row counts
  * CC-S2  readers never observe a partially-visible batch (atomic visibility)
  * CC-S3  mixed read/write hammering raises no errors and ends consistent
"""
from __future__ import annotations

import datetime as dt
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from quant_data import DataReader, ingest

from . import _world

pytestmark = pytest.mark.integration


def _mk_symbol(i: int) -> str:
    # Distinct, well-formed A-share style codes (suffix keeps quality checks quiet).
    return f"6{i:05d}.SH"


def _bars(symbol: str, days, base: float = 10.0) -> list[dict]:
    recs = []
    prev = base
    for d in days:
        close = round(prev * 1.001, 4)
        recs.append(dict(
            date=d, symbol=symbol, open=prev, high=max(prev, close),
            low=min(prev, close), close=close, pre_close=prev,
            volume=1000, amount=round(close * 1000, 2), vwap=close,
            adj_factor=1.0,
        ))
        prev = close
    return recs


def test_cc_s1_parallel_disjoint_ingests_exact_counts(root):
    """8 writers ingest 8 disjoint symbols in parallel; every row lands exactly once."""
    days = _world.trading_days(dt.date(2024, 1, 1), dt.date(2024, 2, 29))
    n_writers = 8
    payloads = {_mk_symbol(i): _bars(_mk_symbol(i), days) for i in range(n_writers)}

    def _write(item):
        sym, recs = item
        return ingest.ingest_bars_daily(records=recs, apply_mapping=False, root=root)

    with ThreadPoolExecutor(max_workers=n_writers) as ex:
        results = list(ex.map(_write, payloads.items()))

    assert all(r.batch_id and not r.skipped for r in results)

    reader = DataReader(root=root)
    for sym, recs in payloads.items():
        got = reader.read_bars_daily(symbols=[sym])
        assert got.num_rows == len(recs), f"{sym}: {got.num_rows} != {len(recs)}"
    total = reader.read_bars_daily(symbols=list(payloads)).num_rows
    assert total == sum(len(r) for r in payloads.values())


def test_cc_s2_reader_never_sees_partial_batch(root):
    """Batch visibility must be atomic: counts only ever move in batch-sized steps.

    A writer commits B batches of exactly K rows for one symbol while readers
    poll the row count. Any observation that is not a multiple of K means a
    half-visible batch (torn read) — the core PIT guarantee would be broken.
    """
    sym = _mk_symbol(99)
    K = 21  # rows per batch (one month of trading days)
    B = 6
    start = dt.date(2024, 1, 1)
    chunks = []
    days_all = _world.trading_days(start, dt.date(2024, 12, 31))
    for b in range(B):
        chunks.append(_bars(sym, days_all[b * K:(b + 1) * K]))
        assert len(chunks[-1]) == K

    stop = threading.Event()
    bad_counts: list[int] = []
    errors: list[BaseException] = []

    def _poll():
        reader = DataReader(root=root)  # one reader (duckdb con) per thread
        try:
            while not stop.is_set():
                n = reader.read_bars_daily(symbols=[sym]).num_rows
                if n % K != 0:
                    bad_counts.append(n)
        except BaseException as e:  # noqa: BLE001 - collected for the assert below
            errors.append(e)

    pollers = [threading.Thread(target=_poll) for _ in range(3)]
    for t in pollers:
        t.start()
    try:
        for chunk in chunks:
            r = ingest.ingest_bars_daily(records=chunk, apply_mapping=False, root=root)
            assert r.batch_id and r.row_count == K
            time.sleep(0.02)  # give pollers a window on each committed state
    finally:
        stop.set()
        for t in pollers:
            t.join(timeout=30)

    assert not errors, f"reader raised under concurrent ingest: {errors[:1]}"
    assert not bad_counts, f"torn read: saw non-batch-multiple counts {sorted(set(bad_counts))}"
    assert DataReader(root=root).read_bars_daily(symbols=[sym]).num_rows == K * B


def test_cc_s3_mixed_read_write_hammer(standard_world):
    """4 writers + 6 readers hammer the same root for ~3s: no errors, consistent end state."""
    root = standard_world["root"]
    days = standard_world["days"]
    deadline = time.monotonic() + 3.0
    errors: list[BaseException] = []
    wrote = {i: 0 for i in range(4)}

    def _writer(i: int):
        sym = _mk_symbol(200 + i)
        try:
            b = 0
            while time.monotonic() < deadline:
                chunk = _bars(sym, days[b % len(days):][:5] or days[:5])
                r = ingest.ingest_bars_daily(records=chunk, apply_mapping=False, root=root)
                assert r.batch_id is not None
                wrote[i] += 1
                b += 5
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    def _reader_loop():
        reader = DataReader(root=root)
        try:
            while time.monotonic() < deadline:
                t = reader.read_bars_daily(symbols=standard_world["symbols"][:3])
                assert t.num_rows > 0
                reader.read_symbols()
                reader.latest_date_in_view("raw.bars_daily")
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=_writer, args=(i,)) for i in range(4)]
    threads += [threading.Thread(target=_reader_loop) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors, f"hammer produced {len(errors)} errors, first: {errors[0]!r}"
    assert sum(wrote.values()) > 0, "writers made no progress"
    # the standard world itself must be untouched by the side traffic
    reader = DataReader(root=root)
    sym0 = standard_world["symbols"][0]
    assert reader.read_bars_daily(symbols=[sym0]).num_rows == len(days)


@pytest.mark.perf
def test_stress_heavy_parallel_ingest_throughput(root):
    """16 writers × ~250 rows × 4 rounds (~16k rows). Reports wall time; exact counts."""
    days = _world.trading_days(dt.date(2024, 1, 1), dt.date(2024, 12, 31))[:250]
    n_writers, rounds = 16, 4

    def _write(i: int):
        for r_i in range(rounds):
            sym = _mk_symbol(1000 + i * 10 + r_i)
            r = ingest.ingest_bars_daily(records=_bars(sym, days),
                                         apply_mapping=False, root=root)
            assert r.batch_id is not None

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=n_writers) as ex:
        list(ex.map(_write, range(n_writers)))
    elapsed = time.perf_counter() - t0

    reader = DataReader(root=root)
    syms = [_mk_symbol(1000 + i * 10 + r_i) for i in range(n_writers) for r_i in range(rounds)]
    assert reader.read_bars_daily(symbols=syms).num_rows == len(days) * n_writers * rounds
    print(f"\n[perf] {n_writers * rounds} batches / {len(days) * n_writers * rounds} rows "
          f"in {elapsed:.2f}s ({len(days) * n_writers * rounds / elapsed:,.0f} rows/s)")
    assert elapsed < 300, "heavy parallel ingest took implausibly long"
