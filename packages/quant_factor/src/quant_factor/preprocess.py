"""Cross-section preprocessing primitives.

Pipeline order (design §11.2):
  ① winsorize → ② zscore → ③ neutralize (residuals) → ④ zscore again

Each function operates on a 1-D Series and returns a same-length Series with
NaN-safe handling. Heavy lifting via NumPy; Polars only at the boundary.
"""
from __future__ import annotations

import numpy as np
import polars as pl


def _to_np(s: pl.Series) -> np.ndarray:
    return s.to_numpy().astype(float)


def winsorize_mad(s: pl.Series, n: float = 5.0) -> pl.Series:
    """Median-Absolute-Deviation clipping.

    `n` is the multiplier of MAD * 1.4826 (so it's comparable to "n-sigma");
    typical 3-5 is fine. If MAD is zero or NaN, returns the series untouched.
    """
    x = _to_np(s)
    if x.size == 0:
        return s
    finite = np.isfinite(x)
    if not finite.any():
        return s
    med = float(np.nanmedian(x[finite]))
    mad = float(np.nanmedian(np.abs(x[finite] - med))) * 1.4826
    if mad <= 0 or not np.isfinite(mad):
        return s
    lo, hi = med - n * mad, med + n * mad
    out = np.where(finite, np.clip(x, lo, hi), x)
    return pl.Series(s.name, out)


def winsorize_quantile(s: pl.Series, q: float = 0.01) -> pl.Series:
    """Symmetric quantile clipping. `q` is the lower tail size (e.g. 0.01 = 1%).

    Both tails are clipped: [q, 1-q]. Useful when MAD is ill-defined.
    """
    x = _to_np(s)
    if x.size == 0:
        return s
    finite = np.isfinite(x)
    if finite.sum() < 4:
        return s
    q = max(min(q, 0.49), 0.0)
    lo = float(np.nanquantile(x[finite], q))
    hi = float(np.nanquantile(x[finite], 1 - q))
    out = np.where(finite, np.clip(x, lo, hi), x)
    return pl.Series(s.name, out)


def zscore(s: pl.Series) -> pl.Series:
    """Subtract mean, divide by std (cross-section z-score)."""
    x = _to_np(s)
    if x.size == 0:
        return s
    finite = np.isfinite(x)
    if not finite.any():
        return s
    mu = float(np.nanmean(x[finite]))
    sd = float(np.nanstd(x[finite]))
    if sd <= 0 or not np.isfinite(sd):
        return pl.Series(s.name, np.where(finite, 0.0, x))
    out = np.where(finite, (x - mu) / sd, x)
    return pl.Series(s.name, out)


def apply_pipeline(
    df: pl.DataFrame,
    *,
    col: str = "value",
    winsorize: str = "mad",
    winsorize_n: float = 5.0,
    standardize: str = "zscore",
) -> pl.DataFrame:
    """Apply ①winsorize + ②standardize to one column. Used by the engine."""
    out = df
    s = out[col]
    if winsorize == "mad":
        s = winsorize_mad(s, winsorize_n)
    elif winsorize == "quantile":
        s = winsorize_quantile(s, winsorize_n)
    if standardize == "zscore":
        s = zscore(s)
    return out.with_columns(s.alias(col))
