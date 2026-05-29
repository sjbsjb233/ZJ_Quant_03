"""Cross-sectional size + industry neutralization.

Why (§11.4): a factor that looks great in raw form may secretly just be a
small-cap or single-industry bet. Neutralization removes the linear influence
of ln(market_cap) + one-hot industry dummies on each cross-section, leaving
only the residual ("pure alpha").

This module:
  - ln_size(reader, symbols, as_of, field): reads circ_mv (or total_mv) and
    returns (symbol, ln_mktcap)
  - industry_at_level(reader, symbols, classification_system, level):
    rolls each symbol's leaf sector code up to the requested level (1/2/3),
    returning (symbol, industry)
  - neutralize_cross_section(df, use_size, use_industry): one-day OLS,
    returns value column replaced by residuals
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl


# ─────────────────────────────────────────────────────────────────────────────
# Size (market cap)
# ─────────────────────────────────────────────────────────────────────────────
def ln_size(
    reader,
    symbols: list[str],
    as_of: dt.date,
    field: str = "circ_mv",
) -> pl.DataFrame:
    """Return [symbol, ln_mktcap] on `as_of` for the requested set.

    `field`:
      - "circ_mv"  : float market cap (A-share convention; default)
      - "total_mv" : total market cap

    Reads bars raw (adjust=none) — market-cap columns are nominal, never
    multiplied by the adjustment factor.
    """
    if field not in ("circ_mv", "total_mv"):
        raise ValueError(f"size field must be circ_mv or total_mv, got {field!r}")

    tbl = reader.read_bars_daily(
        symbols=symbols, start=as_of, end=as_of, adjust="none"
    )
    df = pl.from_arrow(tbl)
    if df.height == 0 or field not in df.columns:
        return pl.DataFrame(
            {"symbol": symbols, "ln_mktcap": [float("nan")] * len(symbols)},
            schema={"symbol": pl.Utf8, "ln_mktcap": pl.Float64},
        )
    return (
        df.select(["symbol", field])
        .with_columns(
            pl.when(pl.col(field) > 0)
            .then(pl.col(field).log())
            .otherwise(None)
            .alias("ln_mktcap")
        )
        .select(["symbol", "ln_mktcap"])
    )


# ─────────────────────────────────────────────────────────────────────────────
# Industry roll-up (level-configurable per design §11.4)
# ─────────────────────────────────────────────────────────────────────────────
_MAX_ROLLUP_DEPTH = 8  # safety cap to prevent infinite loops on bad parent chains


def industry_at_level(
    reader,
    symbols: list[str],
    classification_system: str,
    level: int,
) -> pl.DataFrame:
    """Roll each symbol up to the requested industry level (1/2/3).

    Algorithm:
      - symbols.sector_code may be at the leaf (often L3 or L2).
      - Walk parent_code in sectors[classification_system] until level matches.
      - If the leaf is more coarse than requested (level > leaf.level), return
        None (we can't fabricate detail).
    """
    if level not in (1, 2, 3):
        raise ValueError(f"industry_level must be 1, 2 or 3, got {level!r}")

    syms = pl.from_arrow(reader.read_symbols(symbols=symbols))
    if syms.height == 0:
        return pl.DataFrame(
            {"symbol": symbols, "industry": [None] * len(symbols)},
            schema={"symbol": pl.Utf8, "industry": pl.Utf8},
        )
    syms = syms.select(["symbol", "sector_code"])

    sec = pl.from_arrow(
        reader.read_sectors(classification_system=classification_system)
    )
    # Build a flat code -> (level, parent_code) dict for quick walks.
    lut: dict[str, tuple[int, str | None]] = {}
    if sec.height > 0:
        for row in sec.iter_rows(named=True):
            lut[row["code"]] = (int(row["level"]), row.get("parent_code"))

    def _roll(code: str | None) -> str | None:
        if code is None:
            return None
        cur = code
        for _ in range(_MAX_ROLLUP_DEPTH):
            info = lut.get(cur)
            if info is None:
                return None
            lv, parent = info
            if lv == level:
                return cur
            if lv < level:
                # leaf is coarser than requested level — can't refine
                return None
            if parent is None:
                return None
            cur = parent
        return None

    return syms.with_columns(
        pl.col("sector_code")
        .map_elements(_roll, return_dtype=pl.Utf8)
        .alias("industry")
    ).select(["symbol", "industry"])


# ─────────────────────────────────────────────────────────────────────────────
# OLS residualization on a single cross-section
# ─────────────────────────────────────────────────────────────────────────────
def neutralize_cross_section(
    df: pl.DataFrame,
    *,
    use_size: bool = True,
    use_industry: bool = True,
) -> pl.DataFrame:
    """One-day OLS: value ~ ln_mktcap + industry_dummies -> residual.

    Required columns: symbol, value, optionally ln_mktcap, industry.
    Drops one industry dummy to avoid collinearity with the intercept.

    Returns the same DataFrame with `value` replaced by residuals; rows
    that can't fit (missing covariates) become NaN.
    """
    if not (use_size or use_industry):
        return df

    n = df.height
    if n < 3:
        return df

    y = df["value"].to_numpy().astype(float)

    cols = [np.ones(n)]  # intercept
    if use_size and "ln_mktcap" in df.columns:
        cols.append(df["ln_mktcap"].to_numpy().astype(float))
    if use_industry and "industry" in df.columns:
        ind = df["industry"].to_numpy()
        cats = sorted({c for c in ind if c is not None and c == c})  # drop NaN
        # Drop the first dummy to avoid perfect collinearity.
        for c in cats[1:]:
            cols.append((ind == c).astype(float))

    X = np.column_stack(cols)
    mask = np.isfinite(y) & np.all(np.isfinite(X), axis=1)

    resid = np.full(n, np.nan)
    if mask.sum() > X.shape[1] + 1:
        # least-squares fit + residuals
        beta, *_ = np.linalg.lstsq(X[mask], y[mask], rcond=None)
        resid[mask] = y[mask] - X[mask] @ beta
    return df.with_columns(pl.Series("value", resid))
