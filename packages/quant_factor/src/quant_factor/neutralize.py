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

from .degradation import make_event


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
    ledger=None,
    day=None,
) -> pl.DataFrame:
    """One-day OLS: value ~ ln_mktcap + industry_dummies -> residual.

    Required columns: symbol, value, optionally ln_mktcap, industry.
    Drops one industry dummy to avoid collinearity with the intercept.

    Returns the same DataFrame with `value` replaced by residuals; rows
    that can't fit (missing covariates) become NaN.

    No-silent-degradation (设计 §6): instead of quietly building a zero-dummy
    regression (which pretends to neutralize but only de-means) or lumping
    unknown-industry stocks into the base group, this records structured
    :class:`DegradationEvent`s on the optional ``ledger``:
      - DG_IND_NEUTRAL_SKIPPED (critical): < 2 usable industry categories.
      - DG_IND_PARTIAL (high):  some stocks have a value but no known industry;
        they are set NaN (dropped) rather than polluting the base category.
      - DG_SIZE_DROPPED (high): stocks with a value but missing ln_mktcap are
        excluded from the fit.
    """
    if not (use_size or use_industry):
        return df

    n = df.height
    if n < 3:
        return df

    y = df["value"].to_numpy().astype(float)
    finite_val = np.isfinite(y)

    cols = [np.ones(n)]  # intercept

    if use_size and "ln_mktcap" in df.columns:
        size_arr = df["ln_mktcap"].to_numpy().astype(float)
        cols.append(size_arr)
        # Stocks that HAVE a value but are missing size → will be masked out.
        # Record once per run (de-duped like DG_PRICE_FALLBACK/DG_PREP_NOOP) so a
        # long window can't explode the ledger; the mask still drops them daily.
        size_dropped = int(np.sum(finite_val & ~np.isfinite(size_arr)))
        if size_dropped > 0 and ledger is not None and not ledger.has_code("DG_SIZE_DROPPED"):
            ledger.record(
                make_event(
                    "DG_SIZE_DROPPED",
                    stage="prep",
                    reason=f"{size_dropped} 个标的缺市值(ln_mktcap),回归将剔除(首个命中截面)",
                    impact="缺市值标的被剔除,覆盖度缩水,市值中性化口径对其失效(可能跨多个调仓日)",
                    suggested_action="补齐 raw.bars_daily 的市值字段后重跑",
                    evidence={
                        "affected_symbols": size_dropped,
                        "first_date": str(day) if day is not None else None,
                        "cross_section_size": int(finite_val.sum()),
                    },
                )
            )

    if use_industry and "industry" in df.columns:
        ind = df["industry"].to_numpy()
        cats = sorted({c for c in ind if c is not None and c == c})  # drop NaN
        unknown_mask = np.array([(c is None or c != c) for c in ind])
        n_unknown_with_val = int(np.sum(finite_val & unknown_mask))
        if len(cats) < 2:
            # Not enough categories to form even one dummy: industry term is
            # impossible. Record critical — do NOT fake-neutralize.
            if ledger is not None and not ledger.has_code("DG_IND_NEUTRAL_SKIPPED"):
                ledger.record(
                    make_event(
                        "DG_IND_NEUTRAL_SKIPPED",
                        stage="prep",
                        reason=(
                            f"可用行业类别仅 {len(cats)} 个(<2),无法构造行业哑变量"
                            + (f"(date={day})" if day is not None else "")
                        ),
                        impact="行业中性化未生效,残差等价于仅去均值/去市值,因子可能仍是行业押注",
                        suggested_action="导入对应行业分类数据(reference.sectors)后重跑",
                        evidence={
                            "usable_categories": len(cats),
                            "date": str(day) if day is not None else None,
                            "cross_section_size": int(finite_val.sum()),
                        },
                    )
                )
        else:
            # Drop the first dummy to avoid perfect collinearity.
            for c in cats[1:]:
                cols.append((ind == c).astype(float))
            # Unknown-industry stocks must NOT be silently lumped into the base
            # category — set their value NaN so the mask drops them (every day),
            # and report once per run (de-duped to keep the ledger/DB bounded).
            if n_unknown_with_val > 0:
                y = np.where(unknown_mask, np.nan, y)
                if ledger is not None and not ledger.has_code("DG_IND_PARTIAL"):
                    ledger.record(
                        make_event(
                            "DG_IND_PARTIAL",
                            stage="prep",
                            reason=f"{n_unknown_with_val} 个标的行业未知,已剔除(首个命中截面)",
                            impact="部分标的因行业未知被剔除,覆盖度缩水(可能跨多个调仓日)",
                            suggested_action="补全这些标的的 sector_code / 行业分类后重跑",
                            evidence={
                                "affected_symbols": n_unknown_with_val,
                                "first_date": str(day) if day is not None else None,
                                "usable_categories": len(cats),
                            },
                        )
                    )

    X = np.column_stack(cols)
    mask = np.isfinite(y) & np.all(np.isfinite(X), axis=1)

    resid = np.full(n, np.nan)
    if mask.sum() > X.shape[1] + 1:
        # least-squares fit + residuals
        beta, *_ = np.linalg.lstsq(X[mask], y[mask], rcond=None)
        resid[mask] = y[mask] - X[mask] @ beta
    return df.with_columns(pl.Series("value", resid))
