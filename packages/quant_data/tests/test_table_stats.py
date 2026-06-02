"""table_stats row counting must use an in-db COUNT(*), never materialise.

Regression guard for the 2026-06-02 data-keeper crash loop: ``compute_table_stats``
used ``reader.current_view(table).num_rows`` which copies the whole de-duplicated
table into Arrow/Python, so RSS grew linearly with table size and the kernel
OOM-killed the memory-limited data-keeper on large raw tables (e.g. 18M-row
raw.bars_daily) during the scheduled refresh-stats step. The fix counts rows via
``reader.count_current_view(spec)`` (SELECT COUNT(*), constant memory).
"""
from __future__ import annotations

import datetime as dt

from quant_data import ingest, table_stats
from quant_data.reader import DataReader

from .conftest import daily_bar


def _seed_bars(root, n=3):
    ingest.ingest_bars_daily(
        records=[daily_bar(dt.date(2025, 11, 3) + dt.timedelta(days=i), symbol="X", close=10.0 + i)
                 for i in range(n)],
        apply_mapping=False, root=root,
    )
    return n


def test_compute_table_stats_counts_raw_rows(root):
    n = _seed_bars(root, 3)
    stats = table_stats.compute_table_stats("raw.bars_daily", root=root)
    assert stats["row_count"] == n
    assert stats["file_count"] >= 1
    assert stats["disk_bytes"] > 0


def test_row_count_does_not_materialise_current_view(root, monkeypatch):
    """The anti-pattern guard: if row counting ever reverts to
    ``current_view().num_rows`` this test fails, because current_view is poisoned
    to blow up. count_current_view (the correct path) does not call it."""
    n = _seed_bars(root, 4)

    def _boom(self, *a, **k):
        raise AssertionError("current_view() must not be called to count rows "
                             "(it materialises the whole table and OOMs large raw tables)")

    monkeypatch.setattr(DataReader, "current_view", _boom)
    stats = table_stats.compute_table_stats("raw.bars_daily", root=root)
    assert stats["row_count"] == n


def test_refresh_all_persists_reference_and_raw(root):
    _seed_bars(root, 2)
    rows = table_stats.refresh_all(root=root)
    by = {r["table_name"]: r for r in rows}
    assert by["raw.bars_daily"]["row_count"] == 2
    # reference tables are always present in the ledger (counted from their single file)
    assert "reference.symbols" in by
    assert "reference.sectors" in by
    # and the persisted ledger round-trips
    persisted = {r["table_name"]: r for r in table_stats.get_stats(root=root)}
    assert persisted["raw.bars_daily"]["row_count"] == 2
