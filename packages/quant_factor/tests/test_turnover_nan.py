"""Regression: 换手率 top 组必须剔除 NaN 因子值 (evaluate._turnover)。

np.argsort 把 NaN 排到末尾,direction>0 时 idx=order[-k:] 会把「没有真实因子值」
的标的当成 top 组。IC/分层因 join 后 drop_nulls 而免疫,唯独换手率直接吃 clean,
且「不做中性化」分支不会 drop_nulls —— 自定义因子产出 NaN 时换手率被污染。
"""
from __future__ import annotations

import datetime as dt

import polars as pl

from quant_factor import EvalConfig, FactorEvaluator


def test_turnover_excludes_nan_factor_values():
    d1, d2 = dt.date(2025, 1, 1), dt.date(2025, 1, 2)
    rows: list[dict] = []
    # Same finite universe both days → top-K finite holdings are identical →
    # the *correct* turnover is 0. The NaN names differ across the two days, so
    # if they leak into the top group the turnover jumps well above 0.
    for d, nans in [(d1, ["N1", "N2"]), (d2, ["N3", "N4"])]:
        for sym, val in [("A", 1.0), ("B", 2.0), ("C", 3.0), ("D", 4.0)]:
            rows.append({"date": d, "symbol": sym, "value": val})
        for sym in nans:
            rows.append({"date": d, "symbol": sym, "value": float("nan")})
    clean = pl.DataFrame(rows)

    # _turnover never touches self, so an unbound call keeps the test cheap.
    res = FactorEvaluator._turnover(
        None, clean, [d1, d2], quantiles=2, direction=1, cfg=EvalConfig()
    )
    assert res["mean"] == 0.0, res


def test_turnover_finite_path_unaffected():
    """Pure-finite input keeps the original turnover behaviour."""
    d1, d2 = dt.date(2025, 1, 1), dt.date(2025, 1, 2)
    rows: list[dict] = []
    for sym, val in [("A", 1.0), ("B", 2.0), ("C", 3.0), ("D", 4.0)]:
        rows.append({"date": d1, "symbol": sym, "value": val})
    # Day 2 flips the ranking so the top-2 group fully rotates → turnover 1.0.
    for sym, val in [("A", 4.0), ("B", 3.0), ("C", 2.0), ("D", 1.0)]:
        rows.append({"date": d2, "symbol": sym, "value": val})
    clean = pl.DataFrame(rows)
    res = FactorEvaluator._turnover(
        None, clean, [d1, d2], quantiles=2, direction=1, cfg=EvalConfig()
    )
    assert res["mean"] == 1.0, res
