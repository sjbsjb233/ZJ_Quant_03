"""Multi-factor correlation analysis (design §12).

Two methods:
  - "ic_series"   : correlate two factors' IC time series (Pearson)
  - "factor_value": per-day Spearman on cleaned cross-section values,
                    then average over days

For each pair with |r| >= threshold, pick the higher-ICIR factor as `keep`
and the other as `drop`, with a "why" string describing the comparison.
"""
from __future__ import annotations

from itertools import combinations
from typing import Any, Callable

import numpy as np
import polars as pl

from .base import Factor, RunCancelled
from .config import EvalConfig
from .evaluate import FactorEvaluator


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    mask = np.isfinite(a) & np.isfinite(b)
    if mask.sum() < 3:
        return float("nan")
    if a[mask].std() == 0 or b[mask].std() == 0:
        return float("nan")
    return float(np.corrcoef(a[mask], b[mask])[0, 1])


def _spearman_per_day(a: pl.DataFrame, b: pl.DataFrame) -> list[float]:
    j = (
        a.rename({"value": "value_a"})
        .join(
            b.rename({"value": "value_b"}),
            on=["date", "symbol"],
            how="inner",
        )
        .drop_nulls(["value_a", "value_b"])
    )
    rs: list[float] = []
    for _, sub in j.group_by("date", maintain_order=True):
        x = sub["value_a"].to_numpy().astype(float)
        y = sub["value_b"].to_numpy().astype(float)
        if x.size < 5:
            continue
        xr = np.argsort(np.argsort(x)).astype(float)
        yr = np.argsort(np.argsort(y)).astype(float)
        if xr.std() == 0 or yr.std() == 0:
            continue
        rs.append(float(np.corrcoef(xr, yr)[0, 1]))
    return rs


def correlation_matrix(
    reader,
    factor_instances: dict[str, Factor],
    cfg: EvalConfig,
    uni_def: dict,
    custom_symbols: list[str] | None = None,
    method: str = "ic_series",
    threshold: float = 0.7,
    icir_lookup: dict[str, float] | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
    cancel_cb: Callable[[], bool] | None = None,
    log_cb: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run an N x N correlation analysis on a set of factors.

    `factor_instances` order is preserved in the output matrix.
    """
    ids = list(factor_instances.keys())
    n = len(ids)
    if n < 2:
        return {
            "labels": ids,
            "matrix": [[1.0]] if n == 1 else [],
            "high_pairs": [],
            "summary": {},
        }

    progress = progress_cb or (lambda c, t: None)
    cancelled = cancel_cb or (lambda: False)
    log = log_cb or (lambda m: None)

    ev = FactorEvaluator(reader, progress_cb=None, log_cb=log, cancel_cb=cancel_cb)

    # 1) evaluate each factor once at the shared config -> get IC series + clean values.
    ic_vecs: dict[str, pl.DataFrame] = {}
    clean_vecs: dict[str, pl.DataFrame] = {}
    icir_loc: dict[str, float] = {}
    coverage_total = 0
    for i, (fid, f) in enumerate(factor_instances.items()):
        if cancelled():
            raise RunCancelled()
        log(f"evaluating {fid} for correlation ({i+1}/{n})")
        res = ev.evaluate(f, cfg, uni_def, custom_symbols)
        ic_vecs[fid] = res["ic_series"].select(["date", "rank_ic"])
        clean_vecs[fid] = res["clean_values"]
        icir_loc[fid] = float(res["summary"].get("icir", 0.0))
        coverage_total += int(res["summary"].get("coverage", 0))
        progress(i + 1, n + 1)

    if icir_lookup is None:
        icir_lookup = icir_loc

    # 2) pair-wise correlations.
    M = np.eye(n)
    for i, j in combinations(range(n), 2):
        if cancelled():
            raise RunCancelled()
        a, b = ids[i], ids[j]
        if method == "ic_series":
            m = ic_vecs[a].join(
                ic_vecs[b], on="date", how="inner", suffix="_b"
            )
            x = m["rank_ic"].to_numpy().astype(float)
            y = m["rank_ic_b"].to_numpy().astype(float)
            r = _corr(x, y)
        else:  # factor_value
            rs = _spearman_per_day(clean_vecs[a], clean_vecs[b])
            r = float(np.mean(rs)) if rs else float("nan")
        if not np.isfinite(r):
            r = 0.0
        M[i, j] = M[j, i] = r

    # 3) redundancy candidates
    pairs: list[dict[str, Any]] = []
    for i, j in combinations(range(n), 2):
        r = float(M[i, j])
        if abs(r) >= threshold:
            a, b = ids[i], ids[j]
            ia, ib = float(icir_lookup.get(a, 0.0)), float(icir_lookup.get(b, 0.0))
            if ia >= ib:
                keep, drop_, hi, lo = a, b, ia, ib
            else:
                keep, drop_, hi, lo = b, a, ib, ia
            pairs.append(
                {
                    "a": a,
                    "b": b,
                    "r": round(r, 3),
                    "keep": keep,
                    "drop": drop_,
                    "why": f"ICIR {hi:.2f} > {lo:.2f}",
                }
            )

    # 4) summary stats
    off_diag = [float(M[i, j]) for i, j in combinations(range(n), 2)]
    abs_off = [abs(x) for x in off_diag]
    summary = {
        "pairs_total": len(off_diag),
        "ge_07": sum(1 for r in abs_off if r >= 0.7),
        "ge_05": sum(1 for r in abs_off if r >= 0.5),
        "ge_03": sum(1 for r in abs_off if r >= 0.3),
        "mean_abs_r": round(float(np.mean(abs_off)) if abs_off else 0.0, 3),
        "max_abs_r": round(float(np.max(abs_off)) if abs_off else 0.0, 3),
        "coverage": round(coverage_total / max(1, n), 1),
        "method": method,
        "threshold": threshold,
    }

    return {
        "labels": ids,
        "matrix": [[round(float(M[i, j]), 3) for j in range(n)] for i in range(n)],
        "high_pairs": pairs,
        "summary": summary,
    }
