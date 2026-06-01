"""Tests for the 运行透视 collector (RunTrace) and its engine埋点 (设计 §2).

Two red lines are asserted here:
  1. ``describe`` / ``RunTrace`` produce JSON-friendly, NaN-safe, bounded output.
  2. Turning the trace ON must NOT change a single computed number — the
     collector is a pure side-record (设计 §0.3/§0.4, §5 CI 对拍).
"""
from __future__ import annotations

import datetime as dt
import json

import numpy as np

from quant_factor import EvalConfig, FactorEvaluator
from quant_factor.registry import instantiate
from quant_factor.testing import FakeReader
from quant_factor.trace import (
    MAX_TRACE_DAYS,
    RunTrace,
    StageRecord,
    describe,
)


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


# ─────────────────────────────────────────────────────────────────────────────
# describe()
# ─────────────────────────────────────────────────────────────────────────────
def test_describe_normal_array():
    d = describe(np.array([1.0, 2.0, 3.0, 4.0, 5.0]))
    assert d["n"] == 5 and d["n_nan"] == 0
    assert d["min"] == 1.0 and d["max"] == 5.0 and d["p50"] == 3.0
    assert abs(d["mean"] - 3.0) < 1e-9
    assert d["std"] > 0
    assert d["hist"]["counts"] and len(d["hist"]["edges"]) == 17  # 16 bins -> 17 edges


def test_describe_constant_array_std_zero():
    d = describe(np.array([5.0, 5.0, 5.0, 5.0]))
    assert d["n"] == 4
    assert d["std"] == 0.0
    assert d["min"] == d["max"] == 5.0


def test_describe_all_nan_and_none():
    d = describe(np.array([np.nan, np.nan]))
    assert d["n"] == 0 and d["n_nan"] == 2
    assert d["min"] is None and d["mean"] is None and d["std"] is None
    dn = describe(None)
    assert dn["n"] == 0


def test_describe_drops_non_finite_and_is_json_safe():
    d = describe(np.array([1.0, np.inf, 2.0, -np.inf, 3.0]))
    assert d["n"] == 3 and d["n_nan"] == 2  # inf counted as missing
    # everything must round-trip through json (no NaN/Inf leak)
    json.dumps(d)


def test_describe_histogram_toggle():
    d_on = describe(np.arange(20.0), histogram=True)
    d_off = describe(np.arange(20.0), histogram=False)
    assert "hist" in d_on
    assert "hist" not in d_off
    # the 5-number summary is identical regardless of the histogram flag
    for k in ("n", "min", "p25", "p50", "p75", "max", "mean", "std"):
        assert d_on[k] == d_off[k]


# ─────────────────────────────────────────────────────────────────────────────
# RunTrace
# ─────────────────────────────────────────────────────────────────────────────
def test_runtrace_day_merge_and_dedup_codes():
    tr = RunTrace("frun_x")
    rec = tr.day("2024-01-05")
    rec["ic"] = 0.03
    tr.day("2024-01-05")["turnover"] = 0.2     # same day → same dict
    tr.note_day_degradation("2024-01-05", "DG_IND_PARTIAL")
    tr.note_day_degradation("2024-01-05", "DG_IND_PARTIAL")  # dedup
    j = tr.to_jsonable()
    assert j["n_days"] == 1
    d = j["days"][0]
    assert d["ic"] == 0.03 and d["turnover"] == 0.2
    assert d["degradation_codes"] == ["DG_IND_PARTIAL"]


def test_runtrace_days_sorted_chronologically():
    tr = RunTrace()
    for ds in ["2024-03-01", "2024-01-05", "2024-02-10"]:
        tr.day(ds)
    days = [d["date"] for d in tr.to_jsonable()["days"]]
    assert days == ["2024-01-05", "2024-02-10", "2024-03-01"]


def test_runtrace_truncation():
    tr = RunTrace()
    total = MAX_TRACE_DAYS + 25
    for i in range(total):
        tr.day(dt.date(2020, 1, 1) + dt.timedelta(days=i))
    j = tr.to_jsonable()
    assert j["n_days"] == total           # true count preserved
    assert len(j["days"]) == MAX_TRACE_DAYS
    assert j["days_truncated"] is True


def test_runtrace_stage_lifecycle():
    tr = RunTrace()
    rec = tr.stage("load", foo=1)
    assert isinstance(rec, StageRecord)
    tr.finish_stage(rec, status="ok", duration_ms=12.5, bar=2)
    assert tr.stage_record("load") is rec
    j = tr.to_jsonable()
    s = j["stages"][0]
    assert s["stage"] == "load" and s["status"] == "ok"
    assert s["duration_ms"] == 12.5
    assert s["detail"] == {"foo": 1, "bar": 2}
    json.dumps(j)  # fully serializable


# ─────────────────────────────────────────────────────────────────────────────
# Engine integration — the result must NOT change when trace is on (红线/CI 对拍)
# ─────────────────────────────────────────────────────────────────────────────
def test_trace_does_not_change_results():
    reader = FakeReader()
    factor = instantiate("mom", {"window": 10, "skip": 1})
    cfg = _cfg()

    r_off = FactorEvaluator(reader).evaluate(factor, cfg, UNI)
    r_on = FactorEvaluator(reader, trace=RunTrace("frun_cmp")).evaluate(factor, cfg, UNI)

    assert r_off["trace"] is None
    assert r_on["trace"] is not None
    # every computed product is byte-for-byte identical
    assert r_off["ic_series"].equals(r_on["ic_series"])
    assert r_off["clean_values"].equals(r_on["clean_values"])
    assert r_off["summary"] == r_on["summary"]
    assert r_off["layers"]["layer_means"] == r_on["layers"]["layer_means"]
    assert r_off["turnover"]["mean"] == r_on["turnover"]["mean"]


def test_engine_populates_full_day_record():
    reader = FakeReader()
    factor = instantiate("mom", {"window": 10, "skip": 1})
    tr = RunTrace("frun_pop")
    FactorEvaluator(reader, trace=tr).evaluate(factor, _cfg(), UNI)
    j = tr.to_jsonable()

    assert j["meta"]["factor"] == factor.name
    assert j["meta"]["factor_mode"] == "vectorized"
    assert j["meta"]["n_symbols"] == len(reader.symbols)

    # every day carries the four preprocessing distributions + universe count
    assert j["days"], "expected ≥1 rebalance day"
    for d in j["days"]:
        assert d["dist_raw"]["n"] >= 1
        assert "dist_after_winsorize" in d
        assert "dist_after_standardize" in d
        assert d["dist_after_neutralize"]["n"] >= 1
        assert d["n_in_universe"] >= d["n_clean"]
    # at least one day reaches the layered/IC stages (needs forward returns)
    assert any("layer_returns" in d and "rank_ic" in d for d in j["days"])

    # universe funnel recorded with the canonical step order
    stages = {s["stage"]: s for s in j["stages"]}
    assert "universe_resolve" in stages
    steps = [f["step"] for f in stages["universe_resolve"]["detail"]["funnel"]]
    assert steps[0] == "raw" and steps[-1] == "final"

    # whole artifact is JSON-serializable (artifact red line)
    json.dumps(j)


def test_histogram_disabled_drops_hist_field():
    reader = FakeReader()
    factor = instantiate("mom", {"window": 10, "skip": 1})
    tr = RunTrace("frun_nohist", histogram_enabled=False)
    FactorEvaluator(reader, trace=tr).evaluate(factor, _cfg(), UNI)
    j = tr.to_jsonable()
    assert j["days"]
    for d in j["days"]:
        assert "hist" not in d["dist_raw"]
        assert "hist" not in d["dist_after_neutralize"]


def test_winsorize_clipped_counts_present():
    reader = FakeReader()
    factor = instantiate("mom", {"window": 10, "skip": 1})
    tr = RunTrace("frun_clip")
    FactorEvaluator(reader, trace=tr).evaluate(factor, _cfg(winsorize="mad"), UNI)
    j = tr.to_jsonable()
    # mad winsorize → every day records a clipped {low, high} dict (≥0 each)
    for d in j["days"]:
        wc = d.get("winsorize_clipped")
        assert wc is not None
        assert wc["low"] >= 0 and wc["high"] >= 0
