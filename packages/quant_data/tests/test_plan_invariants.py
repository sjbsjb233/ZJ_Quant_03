"""Data-quality invariant tests (test plan §8).

Each test injects exactly one defect and asserts the matching INV fires (and,
for FATAL ones, that it's classified FATAL). The clean standard world must
produce zero violations.
"""
from __future__ import annotations

import datetime as dt

import pytest

from quant_data import ingest, invariants, storage
from quant_data.metadata import batches, field_registry
from quant_data.schemas import BARS_DAILY, FIELD_REGISTRY

from .conftest import daily_bar, with_meta

pytestmark = pytest.mark.invariant

SYM = "600519.SH"
TD = dt.date(2024, 1, 2)  # a known trading day in the standard world


def _ids(viol):
    return {v.inv for v in viol}


def _by(viol, inv):
    return next(v for v in viol if v.inv == inv)


# --- clean baseline ---------------------------------------------------------
def test_clean_world_has_no_violations(standard_world):
    assert invariants.check_all(root=standard_world["root"]) == []


# --- §8.1 raw value/logic ---------------------------------------------------
def test_inv005_non_trading_day(standard_world):
    root = standard_world["root"]
    ingest.ingest_bars_daily(records=[daily_bar(dt.date(2024, 1, 6), symbol=SYM)],  # Saturday
                             apply_mapping=False, root=root)
    assert "INV-005" in _ids(invariants.check_all(root=root))


def test_inv006_symbol_not_in_reference(standard_world):
    root = standard_world["root"]
    ingest.ingest_bars_daily(records=[daily_bar(TD, symbol="999999.SH")],
                             apply_mapping=False, root=root)
    v = _by(invariants.check_all(root=root), "INV-006")
    assert v.severity == "FATAL"


def test_inv007_negative_volume(standard_world):
    root = standard_world["root"]
    ingest.ingest_bars_daily(records=[daily_bar(TD, symbol=SYM, volume=-1)],
                             apply_mapping=False, root=root)
    assert _by(invariants.check_all(root=root), "INV-007").severity == "FATAL"


def test_inv008_ohlc_ordering(standard_world):
    root = standard_world["root"]
    ingest.ingest_bars_daily(records=[daily_bar(TD, symbol=SYM, high=1.0, low=100.0)],
                             apply_mapping=False, root=root)
    assert _by(invariants.check_all(root=root), "INV-008").severity == "FATAL"


def test_inv009_publish_before_period(standard_world):
    root = standard_world["root"]
    ingest.ingest_fundamentals(records=[{
        "symbol": SYM, "report_period": dt.date(2024, 9, 30), "period_type": "Q4",
        "publish_date": dt.date(2024, 9, 1), "currency": "CNY"}],
        apply_mapping=False, root=root)
    assert _by(invariants.check_all(root=root), "INV-009").severity == "FATAL"


def test_inv010_universe_contradiction(standard_world):
    root = standard_world["root"]
    ingest.ingest_universe(records=[{
        "date": TD, "symbol": SYM, "is_tradable": True, "is_listed": True,
        "is_suspended": True, "st_status": "normal", "in_hs300": True,
        "in_zz500": False, "in_zz1000": False, "in_sse50": True}],
        apply_mapping=False, root=root)
    assert "INV-010" in _ids(invariants.check_all(root=root))


def test_inv011_announce_after_action(standard_world):
    root = standard_world["root"]
    ingest.ingest_corporate_actions(records=[{
        "symbol": SYM, "action_date": dt.date(2024, 3, 1),
        "announce_date": dt.date(2024, 3, 10), "action_type": "cash_dividend",
        "cash_per_share": 1.0}], apply_mapping=False, root=root)
    assert _by(invariants.check_all(root=root), "INV-011").severity == "FATAL"


def test_inv012_calendar_bracket(standard_world):
    root = standard_world["root"]
    ingest.ingest_calendar(records=[{
        "date": dt.date(2024, 1, 2), "market": "CN-A", "is_trading_day": True,
        "prev_trading_day": dt.date(2024, 1, 3),  # prev after date -> violation
        "month_end_trading_day": False, "quarter_end_trading_day": False,
        "year_end_trading_day": False}], apply_mapping=False, root=root)
    assert _by(invariants.check_all(root=root), "INV-012").severity == "FATAL"


# --- §8.1 structural --------------------------------------------------------
def test_inv002_orphan_file_batch(standard_world):
    root = standard_world["root"]
    # write a physical file whose _batch_id was never registered in the ledger
    storage.write_append(BARS_DAILY, with_meta([daily_bar(TD, symbol=SYM)], "ghost-batch"),
                         "ghost-batch", root=root)
    assert _by(invariants.check_all(root=root), "INV-002").severity == "FATAL"


def test_inv003_unknown_source(standard_world):
    root = standard_world["root"]
    bid = batches.register_batch("ghost_src", "raw.bars_daily", root=root)
    batches.finalize_batch(bid, "success", row_count=1, root=root)
    storage.write_append(BARS_DAILY, with_meta([daily_bar(TD, symbol=SYM)], bid, source="ghost_src"),
                         bid, root=root)
    assert _by(invariants.check_all(root=root), "INV-003").severity == "FATAL"


# --- §8.2 metadata ----------------------------------------------------------
def test_inv101_finished_before_started(standard_world):
    root = standard_world["root"]
    bid = batches.register_batch("akshare", "raw.bars_daily", root=root)
    batches.finalize_batch(bid, "success", row_count=1, root=root)
    batches.update_batch(bid, finished_at=dt.datetime(2000, 1, 1), root=root)
    assert _by(invariants.check_all(root=root), "INV-101").severity == "FATAL"


def test_inv103_success_without_row_count(standard_world):
    root = standard_world["root"]
    bid = batches.register_batch("akshare", "raw.bars_daily", root=root)
    batches.finalize_batch(bid, "success", row_count=5, root=root)
    batches.update_batch(bid, row_count=None, root=root)
    assert _by(invariants.check_all(root=root), "INV-103").severity == "FATAL"


def test_inv104_dangling_superseded_by(standard_world):
    root = standard_world["root"]
    bid = batches.register_batch("akshare", "raw.bars_daily", root=root)
    batches.finalize_batch(bid, "success", row_count=1, root=root)
    batches.update_batch(bid, superseded_by="does-not-exist", root=root)
    assert _by(invariants.check_all(root=root), "INV-104").severity == "FATAL"


def test_inv105_supersede_cycle(standard_world):
    root = standard_world["root"]
    a = batches.register_batch("akshare", "raw.bars_daily", root=root)
    b = batches.register_batch("akshare", "raw.bars_daily", root=root)
    for x in (a, b):
        batches.finalize_batch(x, "success", row_count=1, root=root)
    # bypass supersede_batch's guard to forge a cycle directly in the ledger
    batches.update_batch(a, superseded_by=b, root=root)
    batches.update_batch(b, superseded_by=a, root=root)
    assert _by(invariants.check_all(root=root), "INV-105").severity == "FATAL"


def test_inv107_bad_field_status(standard_world):
    root = standard_world["root"]
    rows = storage.read_rows(FIELD_REGISTRY, root=root)
    bad = {n: None for n in FIELD_REGISTRY.all_names()}
    bad.update(table_name="raw.fundamentals", field_name="weird", data_type="DOUBLE",
               status="bogus", description="x")
    rows.append(bad)
    storage.write_rows(FIELD_REGISTRY, rows, root=root)
    assert _by(invariants.check_all(root=root), "INV-107").severity == "FATAL"


# --- §8.3 cross-table -------------------------------------------------------
def test_inv201_zero_volume_not_suspended(standard_world):
    root = standard_world["root"]
    ingest.ingest_bars_daily(records=[daily_bar(TD, symbol=SYM, volume=0)],
                             apply_mapping=False, root=root)
    assert "INV-201" in _ids(invariants.check_all(root=root))


def test_inv202_adj_factor_inconsistent(standard_world):
    root = standard_world["root"]
    # a dividend exists but bars still carry adj_factor=1.0 -> derived disagrees
    ingest.ingest_corporate_actions(records=[{
        "symbol": SYM, "action_date": dt.date(2024, 2, 1),
        "announce_date": dt.date(2024, 1, 10), "action_type": "cash_dividend",
        "cash_per_share": 5.0}], apply_mapping=False, root=root)
    assert _by(invariants.check_all(root=root), "INV-202").severity == "WARN"


def test_inv203_bar_after_delist(standard_world):
    root = standard_world["root"]
    # 600145.SH delisted 2024-06-28; a bar after that date violates INV-203
    ingest.ingest_bars_daily(records=[daily_bar(dt.date(2024, 7, 1), symbol="600145.SH")],
                             apply_mapping=False, root=root)
    assert _by(invariants.check_all(root=root), "INV-203").severity == "FATAL"


# --- OOM guard (2026-06 memory audit) ---------------------------------------
def test_guard_blocks_oversized_sweep(standard_world, monkeypatch):
    """check_all must fail fast (not OOM) when the current view exceeds the
    row budget; QUANT_INVARIANTS_ALLOW_LARGE overrides."""
    root = standard_world["root"]
    # tiny budget -> the standard world already exceeds it
    monkeypatch.setenv("QUANT_INVARIANTS_MAX_ROWS", "1")
    monkeypatch.delenv("QUANT_INVARIANTS_ALLOW_LARGE", raising=False)
    with pytest.raises(RuntimeError, match="would materialise"):
        invariants.check_all(root=root)
    # explicit override lets the sweep run again
    monkeypatch.setenv("QUANT_INVARIANTS_ALLOW_LARGE", "1")
    assert invariants.check_all(root=root) == []
