"""Cross-section preprocessing primitives.

Pipeline order (design §11.2):
  ① winsorize → ② zscore → ③ neutralize (residuals) → ④ zscore again

Each function operates on a 1-D Series and returns a same-length Series with
NaN-safe handling. Heavy lifting via NumPy; Polars only at the boundary.
"""
from __future__ import annotations

import numpy as np
import polars as pl

from .degradation import make_event


def _to_np(s: pl.Series) -> np.ndarray:
    return s.to_numpy().astype(float)


def _note_prep_noop(ledger, reason: str, day=None) -> None:
    """Record DG_PREP_NOOP once (low severity, de-duped to avoid per-day flood)."""
    if ledger is None or ledger.has_code("DG_PREP_NOOP"):
        return
    ledger.record(
        make_event(
            "DG_PREP_NOOP",
            stage="prep",
            reason=reason,
            impact="对应截面去极值/标准化未实际生效(原样透传),预处理口径与配置不符",
            suggested_action="多为该截面常量/退化所致;若频繁出现请核查因子取值分布",
            evidence={"date": str(day) if day is not None else None, "detail": reason},
        )
    )


def winsorize_mad(s: pl.Series, n: float = 5.0, *, ledger=None, day=None) -> pl.Series:
    """Median-Absolute-Deviation clipping.

    `n` is the multiplier of MAD * 1.4826 (so it's comparable to "n-sigma");
    typical 3-5 is fine. If MAD is zero or NaN, returns the series untouched
    (and records DG_PREP_NOOP when a ledger is supplied).
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
        _note_prep_noop(ledger, "MAD=0(截面近似常量),去极值未生效", day)
        return s
    lo, hi = med - n * mad, med + n * mad
    out = np.where(finite, np.clip(x, lo, hi), x)
    return pl.Series(s.name, out)


def winsorize_quantile(s: pl.Series, q: float = 0.01, *, ledger=None, day=None) -> pl.Series:
    """Symmetric quantile clipping. `q` is the lower tail size (e.g. 0.01 = 1%).

    Both tails are clipped: [q, 1-q]. Useful when MAD is ill-defined.
    """
    x = _to_np(s)
    if x.size == 0:
        return s
    finite = np.isfinite(x)
    if finite.sum() < 4:
        _note_prep_noop(ledger, "有效值不足 4 个,分位去极值未生效", day)
        return s
    q = max(min(q, 0.49), 0.0)
    lo = float(np.nanquantile(x[finite], q))
    hi = float(np.nanquantile(x[finite], 1 - q))
    out = np.where(finite, np.clip(x, lo, hi), x)
    return pl.Series(s.name, out)


def zscore(s: pl.Series, *, ledger=None, day=None) -> pl.Series:
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
        _note_prep_noop(ledger, "标准差=0(截面近似常量),标准化退化为去均值", day)
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
    ledger=None,
    day=None,
    capture: dict | None = None,
    capture_histogram: bool = True,
) -> pl.DataFrame:
    """Apply ①winsorize + ②standardize to one column. Used by the engine.

    ``capture`` (运行透视 §2.2): when a dict is supplied, this single code path
    *additionally* records the post-winsorize / post-standardize distributions and
    the削尾计数 into it — a pure side-record that never alters the returned frame.
    Passing ``capture=None`` (the default) keeps the original behaviour byte-for-byte.
    """
    out = df
    s = out[col]
    # `pre` (pre-winsorize snapshot) is only needed for the clip counts, which
    # only apply under mad/quantile — skip the copy entirely otherwise (Copilot).
    pre = (
        s.to_numpy().astype(float)
        if (capture is not None and winsorize in ("mad", "quantile"))
        else None
    )
    if winsorize == "mad":
        s = winsorize_mad(s, winsorize_n, ledger=ledger, day=day)
    elif winsorize == "quantile":
        s = winsorize_quantile(s, winsorize_n, ledger=ledger, day=day)
    if capture is not None and winsorize in ("mad", "quantile"):
        from .trace import describe

        post_w = s.to_numpy().astype(float)
        # 削尾计数：与原值相比被压低/抬高的个数（NaN 安全）。
        eps = 1e-12
        valid = np.isfinite(pre) & np.isfinite(post_w)
        low = int(np.sum(valid & (post_w < pre - eps)))
        high = int(np.sum(valid & (post_w > pre + eps)))
        capture["winsorize_clipped"] = {"low": low, "high": high}
        capture["after_winsorize"] = describe(post_w, histogram=capture_histogram)
    if standardize == "zscore":
        s = zscore(s, ledger=ledger, day=day)
    if capture is not None and standardize == "zscore":
        from .trace import describe

        capture["after_standardize"] = describe(
            s.to_numpy().astype(float), histogram=capture_histogram
        )
    return out.with_columns(s.alias(col))
