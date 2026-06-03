"""SQL-pushdown pagination + count + post_filter dedup semantics.

Covers the fix for the 2026-05-23 OOM where ``/tables/{name}/rows`` was
materialising the whole table to Python before slicing. These tests pin the
guarantees of the post-fix API:

* paged reads + concat equal the unpaged read, in the same order
* ``count_current_view`` matches ``current_view().num_rows`` under any filters
* a ``_source`` post-filter never resurrects a row that lost the dedup rank
* ``read_batch`` honours limit/offset via SQL (not in-memory slice)
* fundamentals' PIT dedup order is preserved when paged
"""
from __future__ import annotations

import datetime as dt

import pytest

from quant_data import ingest
from quant_data.metadata import sources
from quant_data.schemas import get_spec

from .conftest import daily_bar, fundamental

pytestmark = pytest.mark.unit


# --- helpers ---------------------------------------------------------------
def _ingest_daily_grid(root, n_symbols=5, n_days=10):
    """Ingest a deterministic n_symbols × n_days daily bars grid for paging tests."""
    start = dt.date(2024, 6, 3)  # a Monday
    rows = []
    for s in range(n_symbols):
        sym = f"{600100 + s:06d}.SH"
        for d in range(n_days):
            day = start + dt.timedelta(days=d)
            rows.append(daily_bar(day, symbol=sym, close=100.0 + s + d * 0.1))
    ingest.ingest_bars_daily(records=rows, apply_mapping=False, root=root)
    return n_symbols * n_days


# --- §1 pagination consistency --------------------------------------------
def test_paged_concat_equals_unpaged(root, reader):
    total = _ingest_daily_grid(root)
    spec = get_spec("raw.bars_daily")
    order = ", ".join(spec.primary_key)

    full = reader.current_view("raw.bars_daily", order_by=order).to_pylist()
    assert len(full) == total

    page_size = 7  # deliberately not a divisor so the last page is short
    pages = []
    offset = 0
    while offset < total:
        p = reader.current_view(
            "raw.bars_daily", order_by=order, limit=page_size, offset=offset
        ).to_pylist()
        if not p:
            break
        pages.extend(p)
        offset += page_size
    assert pages == full, "paged concat must reproduce the unpaged read exactly"


def test_limit_zero_offset_returns_empty(root, reader):
    _ingest_daily_grid(root, n_symbols=2, n_days=3)
    spec = get_spec("raw.bars_daily")
    out = reader.current_view(
        "raw.bars_daily", order_by=", ".join(spec.primary_key), limit=0, offset=0,
    )
    assert out.num_rows == 0


def test_offset_past_end_returns_empty(root, reader):
    n = _ingest_daily_grid(root, n_symbols=2, n_days=3)
    spec = get_spec("raw.bars_daily")
    out = reader.current_view(
        "raw.bars_daily", order_by=", ".join(spec.primary_key),
        limit=10, offset=n + 100,
    )
    assert out.num_rows == 0


# --- §2 count_current_view ------------------------------------------------
def test_count_matches_num_rows_no_filters(root, reader):
    n = _ingest_daily_grid(root)
    spec = get_spec("raw.bars_daily")
    assert reader.count_current_view(spec) == n
    assert reader.current_view("raw.bars_daily").num_rows == n


def test_count_matches_num_rows_with_pre_filter(root, reader):
    _ingest_daily_grid(root, n_symbols=4, n_days=8)
    spec = get_spec("raw.bars_daily")
    pre = ["t.symbol = '600101.SH'"]
    cnt = reader.count_current_view(spec, filters=pre)
    rows = reader.current_view("raw.bars_daily", filters=pre).num_rows
    assert cnt == rows == 8


def test_count_zero_when_no_data(root, reader):
    spec = get_spec("raw.bars_daily")
    assert reader.count_current_view(spec) == 0


# --- §3 post_filter dedup semantics ---------------------------------------
def test_post_filter_does_not_resurrect_loser(root, reader):
    """A _source post-filter must select among dedup WINNERS, not raw rows.

    Without dedup-order-aware filtering, asking for source=tushare_pro for a
    key that akshare won would silently return tushare_pro's losing row -- a
    correctness break that 4.1's note about "post-filter must come after
    rn = 1" exists to prevent.
    """
    D = dt.date(2025, 11, 3)
    sources.register_source("tushare_pro", priority=50, root=root)  # below akshare(100)
    ingest.ingest_bars_daily(records=[daily_bar(D, close=10.0)],
                             source="akshare", root=root)
    ingest.ingest_bars_daily(records=[daily_bar(D, close=99.0)],
                             source="tushare_pro", root=root)

    spec = get_spec("raw.bars_daily")
    # Without post_filter, akshare wins (priority 100 > 50).
    base = reader.current_view("raw.bars_daily").to_pylist()
    assert [r["close"] for r in base] == [10.0]

    # With a tushare-only post_filter, the loser must NOT resurface.
    tushare_only = reader.current_view(
        "raw.bars_daily", post_filters=["_source = 'tushare_pro'"],
    ).to_pylist()
    assert tushare_only == []
    assert reader.count_current_view(
        spec, post_filters=["_source = 'tushare_pro'"]) == 0

    # akshare-only matches the winner.
    akshare_only = reader.current_view(
        "raw.bars_daily", post_filters=["_source = 'akshare'"],
    ).to_pylist()
    assert [r["close"] for r in akshare_only] == [10.0]


def test_post_filter_batch_id_picks_winner_only(root, reader):
    """_batch_id post-filter selects only when that batch is the winning row."""
    D = dt.date(2025, 11, 3)
    losing = ingest.ingest_bars_daily(records=[daily_bar(D, close=10.0)], root=root)
    winning = ingest.ingest_bars_daily(records=[daily_bar(D, close=11.0)], root=root)

    out = reader.current_view(
        "raw.bars_daily", post_filters=[f"_batch_id = '{losing.batch_id}'"],
    ).to_pylist()
    assert out == [], "losing batch must not be selectable through post_filter"

    out = reader.current_view(
        "raw.bars_daily", post_filters=[f"_batch_id = '{winning.batch_id}'"],
    ).to_pylist()
    assert [r["close"] for r in out] == [11.0]


# --- §4 fundamentals PIT dedup_order preserved under pagination -----------
def test_fundamentals_pit_dedup_order_paged(root, reader):
    """publish_date DESC must win over later _ingested_at when paginated.

    Tests the dedup_order parameter actually flows through the paged path
    (regression: dropping dedup_order would let a backfilled-late older
    revision leak in).
    """
    # Older revision is INGESTED later (simulating a backfill) but has the
    # earlier publish_date.
    later_pub = fundamental(symbol="600101.SH",
                            publish_date=dt.date(2024, 10, 28), revenue=200.0)
    earlier_pub = fundamental(symbol="600101.SH",
                              publish_date=dt.date(2024, 10, 1), revenue=100.0)
    ingest.ingest_fundamentals(records=[later_pub], root=root)
    ingest.ingest_fundamentals(records=[earlier_pub], root=root)  # backfilled later

    dedup = ("_src_priority DESC, publish_date DESC, _ingested_at DESC, "
             "_batch_id DESC")
    pre = ["t.publish_date <= DATE '2024-11-01'"]
    page = reader.current_view(
        "raw.fundamentals", filters=pre, dedup_order=dedup,
        order_by="symbol, report_period", limit=10, offset=0,
    ).to_pylist()
    assert len(page) == 1
    assert page[0]["revenue"] == 200.0, "publish_date DESC must win over _ingested_at"

    spec = get_spec("raw.fundamentals")
    assert reader.count_current_view(spec, filters=pre, dedup_order=dedup) == 1


# --- §5 read_batch limit/offset pushdown ----------------------------------
def test_read_batch_limit_offset_pushdown(root, reader):
    """read_batch must respect limit/offset; count_batch returns the total."""
    rows = [daily_bar(dt.date(2024, 6, 3) + dt.timedelta(days=i),
                      symbol=f"{600100 + (i % 3):06d}.SH", close=10.0 + i)
            for i in range(20)]
    res = ingest.ingest_bars_daily(records=rows, apply_mapping=False, root=root)

    total = reader.count_batch(res.batch_id, "raw.bars_daily")
    assert total == 20

    page1 = reader.read_batch(res.batch_id, "raw.bars_daily", limit=5, offset=0)
    page2 = reader.read_batch(res.batch_id, "raw.bars_daily", limit=5, offset=5)
    full = reader.read_batch(res.batch_id, "raw.bars_daily")
    assert page1.num_rows == 5 and page2.num_rows == 5
    # Ordered slices must equal the same ranges of the full read.
    assert page1.to_pylist() == full.to_pylist()[:5]
    assert page2.to_pylist() == full.to_pylist()[5:10]


def test_read_batch_no_limit_unchanged(root, reader):
    """No limit -> previous behaviour: every row in the batch."""
    rows = [daily_bar(dt.date(2024, 6, 3) + dt.timedelta(days=i),
                      symbol="600101.SH", close=10.0 + i)
            for i in range(7)]
    res = ingest.ingest_bars_daily(records=rows, apply_mapping=False, root=root)
    tbl = reader.read_batch(res.batch_id, "raw.bars_daily")
    assert tbl.num_rows == 7


# --- §full-view pagination (one-off OOM follow-up) -------------------------
def test_read_full_view_paged_concat_and_count(root, reader):
    """read_full_view must page in SQL + count_full_view match the row count,
    so the '全量(含作废)' export never materialises the whole physical table
    (the +8.3GB /rows/all OOM the perf probe found)."""
    n = _ingest_daily_grid(root, n_symbols=4, n_days=8)  # 32 physical rows
    spec = get_spec("raw.bars_daily")
    order = ", ".join(spec.primary_key) + ", _batch_id"

    assert reader.count_full_view("raw.bars_daily") == n
    full = reader.read_full_view("raw.bars_daily", order_by=order).to_pylist()
    assert len(full) == n

    page = 5
    pages, offset = [], 0
    while offset < n:
        p = reader.read_full_view(
            "raw.bars_daily", order_by=order, limit=page, offset=offset
        ).to_pylist()
        if not p:
            break
        pages.extend(p)
        offset += page
    assert pages == full, "paged full-view concat must reproduce the unpaged read"


def test_count_full_view_respects_filters(root, reader):
    _ingest_daily_grid(root, n_symbols=4, n_days=8)
    one = reader.count_full_view("raw.bars_daily", filters=["t.symbol = '600100.SH'"])
    assert one == 8
