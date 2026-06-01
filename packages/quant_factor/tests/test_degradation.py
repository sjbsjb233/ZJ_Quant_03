"""Tests for the preflight gate + degradation台账 (设计 §13 acceptance).

Uses the in-memory FakeReader to reproduce each missing-data scenario and
asserts the core 红线 (设计 §13): "industry/size data missing" can NEVER end as
status=success — it is either blocked (strict) or degraded (permissive), and is
always visible in the台账.
"""
from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from quant_factor import EvalConfig, FactorEvaluator
from quant_factor.degradation import DegradationLedger, make_event
from quant_factor.preflight import (
    PreflightBlocked,
    evaluate_checks,
    run_preflight,
)
from quant_factor.registry import instantiate
from quant_factor.testing import FakeReader


UNI = {
    "universe_id": "preset:hs300", "kind": "preset_index", "preset_flag": "in_hs300",
    "exclude_st": True, "exclude_suspended": True, "min_list_days": 0,
    "classification_system": "SW",
}


def _cfg(**kw) -> EvalConfig:
    base = dict(
        universe_id="preset:hs300",
        start=dt.date(2024, 1, 2), end=dt.date(2024, 3, 1),
        horizons=(1, 5), primary_horizon=5, rebalance="W",
        neutralize_size=True, neutralize_industry=True,
        return_price="close", winsorize="mad", standardize="zscore",
    )
    base.update(kw)
    return EvalConfig(**base)


def _symbols(reader: FakeReader) -> list[str]:
    return list(reader.symbols)


def _evaluate(reader, cfg, profile, uni=UNI):
    """Run preflight + evaluate exactly as the worker would; return (status, ledger)."""
    factor = instantiate("mom", {"window": 10, "skip": 1})
    try:
        _, ledger = run_preflight(
            reader, cfg, uni, _symbols(reader), cfg.start, cfg.end, profile=profile
        )
    except PreflightBlocked as pb:
        return "failed", pb
    ev = FactorEvaluator(reader, ledger=ledger)
    ev.evaluate(factor, cfg, uni)
    return ("degraded" if ledger.count else "success"), ledger


# ─────────────────────────────────────────────────────────────────────────────
# Ledger primitives
# ─────────────────────────────────────────────────────────────────────────────
def test_ledger_levels():
    le = DegradationLedger()
    assert le.level() == "none" and le.count == 0 and not le.has_critical
    le.record(make_event("DG_PREP_NOOP", stage="prep", reason="r", impact="i", suggested_action="a"))
    assert le.level() == "partial" and not le.has_critical
    le.record(make_event("DG_IND_NEUTRAL_SKIPPED", stage="prep", reason="r", impact="i", suggested_action="a"))
    assert le.level() == "severe" and le.has_critical and le.count == 2
    assert le.has_code("DG_PREP_NOOP") and not le.has_code("DG_NOPE")
    j = le.to_jsonable()
    assert j[0]["code"] == "DG_PREP_NOOP" and j[1]["severity"] == "critical"


def test_make_event_pulls_catalogue_defaults():
    ev = make_event("PF_NEU_IND", stage="preflight", reason="r", impact="i", suggested_action="a")
    assert ev.severity == "critical"
    assert ev.category == "neutralization"
    assert ev.config_key == "neutralize_industry"
    assert ev.occurred_at.endswith("Z")


# ─────────────────────────────────────────────────────────────────────────────
# §13 case 6 — full data → success, zero degradations
# ─────────────────────────────────────────────────────────────────────────────
def test_full_data_is_success():
    status, ledger = _evaluate(FakeReader(), _cfg(), "strict")
    assert status == "success"
    assert ledger.count == 0
    assert ledger.level() == "none"


# ─────────────────────────────────────────────────────────────────────────────
# §13 case 1 — industry data empty
# ─────────────────────────────────────────────────────────────────────────────
def test_industry_missing_strict_blocks():
    r = FakeReader(with_sectors=False, sector_coverage=0.0)
    status, blocked = _evaluate(r, _cfg(), "strict")
    assert status == "failed"
    assert "PF_NEU_IND" in [x.check_id for x in blocked.results]


def test_industry_missing_permissive_degrades_critical():
    r = FakeReader(with_sectors=False, sector_coverage=0.0)
    status, ledger = _evaluate(r, _cfg(), "permissive")
    assert status == "degraded"
    codes = {e.code for e in ledger.events}
    assert "DG_IND_NEUTRAL_SKIPPED" in codes  # runtime critical fired
    assert ledger.has_critical and ledger.level() == "severe"


# ─────────────────────────────────────────────────────────────────────────────
# §13 case 2 — size (circ_mv) missing
# ─────────────────────────────────────────────────────────────────────────────
def test_size_missing_strict_blocks():
    # circ_mv absent and size_field defaults to circ_mv
    r = FakeReader(with_circ_mv=False)
    status, blocked = _evaluate(r, _cfg(neutralize_industry=False), "strict")
    assert status == "failed"
    assert "PF_NEU_SIZE" in [x.check_id for x in blocked.results]


def test_size_missing_permissive_degrades():
    r = FakeReader(with_circ_mv=False)
    status, ledger = _evaluate(r, _cfg(neutralize_industry=False), "permissive")
    assert status == "degraded"
    # PF_NEU_SIZE degrade event recorded; runtime DG_SIZE_DROPPED also plausible.
    assert any(e.code in ("PF_NEU_SIZE", "DG_SIZE_DROPPED") for e in ledger.events)


# ─────────────────────────────────────────────────────────────────────────────
# §13 case 3 — partial industry coverage (60%)
# ─────────────────────────────────────────────────────────────────────────────
def test_partial_industry_coverage_strict_blocks():
    r = FakeReader(sector_coverage=0.60)
    status, blocked = _evaluate(r, _cfg(), "strict")
    # < 95% threshold → block under strict
    assert status == "failed"
    assert "PF_NEU_IND" in [x.check_id for x in blocked.results]


def test_partial_industry_coverage_permissive_partial_drop():
    r = FakeReader(sector_coverage=0.60)
    status, ledger = _evaluate(r, _cfg(), "permissive")
    assert status == "degraded"
    codes = {e.code for e in ledger.events}
    # unknown-industry stocks are dropped + reported (DG_IND_PARTIAL), not lumped
    assert "DG_IND_PARTIAL" in codes


# ─────────────────────────────────────────────────────────────────────────────
# §13 case 4 — vwap missing
# ─────────────────────────────────────────────────────────────────────────────
def test_vwap_missing_strict_blocks():
    r = FakeReader(with_vwap=False)
    status, blocked = _evaluate(
        r, _cfg(neutralize_size=False, neutralize_industry=False, return_price="vwap"), "strict"
    )
    assert status == "failed"
    assert "PF_RET_PRICE" in [x.check_id for x in blocked.results]


def test_vwap_missing_permissive_fallback():
    r = FakeReader(with_vwap=False)
    status, ledger = _evaluate(
        r, _cfg(neutralize_size=False, neutralize_industry=False, return_price="vwap"), "permissive"
    )
    assert status == "degraded"
    codes = {e.code for e in ledger.events}
    assert "DG_PRICE_FALLBACK" in codes or "PF_RET_PRICE" in codes


# ─────────────────────────────────────────────────────────────────────────────
# §13 case 5 — illegal winsorize → block in BOTH profiles
# ─────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("profile", ["strict", "permissive"])
def test_illegal_winsorize_always_blocks(profile):
    r = FakeReader()
    status, blocked = _evaluate(
        r, _cfg(winsorize="rank", neutralize_size=False, neutralize_industry=False), profile
    )
    assert status == "failed"
    assert "PF_PREP_METHOD" in [x.check_id for x in blocked.results]


# ─────────────────────────────────────────────────────────────────────────────
# Window / structural
# ─────────────────────────────────────────────────────────────────────────────
def test_window_too_short_blocks():
    r = FakeReader(n_days=3)
    status, blocked = _evaluate(
        r, _cfg(horizons=(1, 5), neutralize_size=False, neutralize_industry=False), "permissive"
    )
    assert status == "failed"
    assert "PF_WINDOW" in [x.check_id for x in blocked.results]


# ─────────────────────────────────────────────────────────────────────────────
# Universe filter no-op recording (设计 §6 DG_UNI_FILTER_NOOP)
# ─────────────────────────────────────────────────────────────────────────────
def test_universe_filter_noop_recorded_at_runtime():
    # exclude_st requested but st_status column absent → runtime no-op event
    r = FakeReader(with_st=False)
    cfg = _cfg(neutralize_size=False, neutralize_industry=False)
    _, ledger = run_preflight(r, cfg, UNI, _symbols(r), cfg.start, cfg.end, profile="permissive")
    factor = instantiate("mom", {"window": 10, "skip": 1})
    ev = FactorEvaluator(r, ledger=ledger)
    ev.evaluate(factor, cfg, UNI)
    assert ledger.has_code("DG_UNI_FILTER_NOOP")


# ─────────────────────────────────────────────────────────────────────────────
# neutralize_cross_section unit behaviour
# ─────────────────────────────────────────────────────────────────────────────
def test_neutralize_records_skip_on_single_industry():
    from quant_factor.neutralize import neutralize_cross_section

    df = pl.DataFrame({
        "symbol": [f"s{i}" for i in range(10)],
        "value": [float(i) for i in range(10)],
        "ln_mktcap": [float(i) + 1.0 for i in range(10)],
        "industry": ["801010"] * 10,  # only ONE category → cannot neutralize
    })
    le = DegradationLedger()
    neutralize_cross_section(df, use_size=True, use_industry=True, ledger=le, day="2024-01-02")
    assert le.has_code("DG_IND_NEUTRAL_SKIPPED")
    assert le.has_critical


def test_neutralize_records_partial_unknown_industry():
    from quant_factor.neutralize import neutralize_cross_section

    inds = ["801010", "801020", None, "801010", "801020", None, "801010", "801020", "801010", "801020"]
    df = pl.DataFrame({
        "symbol": [f"s{i}" for i in range(10)],
        "value": [float(i) for i in range(10)],
        "industry": inds,
    })
    le = DegradationLedger()
    out = neutralize_cross_section(df, use_size=False, use_industry=True, ledger=le, day="2024-01-02")
    assert le.has_code("DG_IND_PARTIAL")
    # the two unknown-industry rows are NaN'd out (dropped by caller), not lumped
    n_nan = out["value"].is_nan().sum() + out["value"].is_null().sum()
    assert n_nan >= 2


# ─────────────────────────────────────────────────────────────────────────────
# evaluate_checks classification (dry-run shape)
# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# §13 case 7 — data fingerprint changes when data is backfilled (设计 §9.1)
# ─────────────────────────────────────────────────────────────────────────────
def test_data_fingerprint_changes_when_data_completes():
    from quant_factor.cache import data_fingerprint
    from quant_factor.config import config_signature

    cfg = _cfg()
    fp_missing = data_fingerprint(FakeReader(with_sectors=False, with_circ_mv=False), cfg)
    fp_full = data_fingerprint(FakeReader(), cfg)
    assert fp_missing != fp_full
    # → the cache signature flips too, so a补齐后重跑 cannot reuse a stale key
    sig_missing = config_signature(cfg, extra=[fp_missing])
    sig_full = config_signature(cfg, extra=[fp_full])
    assert sig_missing != sig_full


def test_evaluate_checks_classifies_block_vs_degrade():
    r = FakeReader(with_sectors=False, sector_coverage=0.0, with_st=False)
    cfg = _cfg()
    results, policy = evaluate_checks(
        r, cfg, UNI, _symbols(r), cfg.start, cfg.end, profile="strict"
    )
    by = {x.check_id: x.status for x in results}
    assert by.get("PF_NEU_IND") == "block"      # critical, strict → block
    assert by.get("PF_UNI_ST") == "degrade"      # high, strict → degrade
