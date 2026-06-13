"""Forward returns — strict t -> t+h, no look-ahead.

Operates on post-adjusted prices (continuous through corporate actions).
Returns a DataFrame [date, symbol, fwd_ret] where on date t the value is the
price ratio price[t+h] / price[t] - 1, computed per symbol via Polars'
.shift(-h).over("symbol"). Rows near the edge become NaN.

内存治理 (《内存治理总体方案》WP3.2): the signature changed from *reader* to
*panel* — the function is now a pure derivation over the evaluation panel the
engine read ONCE, instead of issuing its own full-window governed read per
horizon (the old shape cost H+1 redundant full reads per evaluation, 事故
档案: 06-04/05 factor OOM). Same shifts, same operators, same order ⇒ the
output floats are bit-identical to the read-per-call implementation.
"""
from __future__ import annotations

import polars as pl

from .degradation import make_event


def forward_returns(
    panel: pl.DataFrame,
    horizon: int,
    price: str = "close",
    ledger=None,
) -> pl.DataFrame:
    """Compute (date, symbol, fwd_ret) for horizon h from the evaluation panel.

    ``panel``: POST-adjusted daily bars covering the evaluation window — the
    one panel ``FactorEvaluator.evaluate`` reads at the top (WP3.2). Must carry
    [date, symbol] + the price column(s) for the requested口径.

    `price`:
      - "close"     : per-day adj close (post)
      - "vwap"      : per-day adj vwap (post)
      - "open_next" : approximates "buy at next open, hold h-1 days"; we use
                      open shift(-1) -> close shift(-h). Close-fallback if
                      open is missing for some bars.
    """
    if panel.height == 0:
        return pl.DataFrame(
            {"date": [], "symbol": [], "fwd_ret": []},
            schema={"date": pl.Date, "symbol": pl.Utf8, "fwd_ret": pl.Float64},
        )
    bars = panel.sort(["symbol", "date"])
    if price == "vwap":
        # vwap口径要求 vwap 列存在且不是全空;否则静默回退会让"按 VWAP 计算"
        # 的配置悄悄变成 CLOSE。记录 DG_PRICE_FALLBACK(去重:多 horizon 只记一次)。
        vwap_ok = "vwap" in bars.columns and bars["vwap"].drop_nulls().len() > 0
        if vwap_ok:
            col = "vwap"
        else:
            col = "close"
            if ledger is not None and not ledger.has_code("DG_PRICE_FALLBACK"):
                ledger.record(
                    make_event(
                        "DG_PRICE_FALLBACK",
                        stage="compute",
                        reason="vwap 列缺失或全空,前向收益回退使用 close",
                        impact="收益口径实际为 CLOSE 而非配置的 VWAP,IC/分层结果口径不符",
                        suggested_action="补齐 raw.bars_daily.vwap 后重跑;或显式改用 close 口径",
                        evidence={
                            "requested": "vwap",
                            "used": "close",
                            "vwap_present": "vwap" in bars.columns,
                        },
                    )
                )
    else:
        col = "close"

    if price == "open_next":
        # Use next open as entry, t+h close as exit; close-fallback when
        # open is NaN on the entry day.
        if "open" not in bars.columns:
            col_entry_expr = pl.col("close").shift(-1).over("symbol")
        else:
            col_entry_expr = (
                pl.coalesce(pl.col("open"), pl.col("close")).shift(-1).over("symbol")
            )
        col_exit_expr = pl.col("close").shift(-horizon).over("symbol")
        fwd = (col_exit_expr / col_entry_expr - 1.0).alias("fwd_ret")
    else:
        fwd_expr = (
            pl.col(col).shift(-horizon).over("symbol") / pl.col(col) - 1.0
        ).alias("fwd_ret")
        fwd = fwd_expr

    return bars.with_columns(fwd).select(["date", "symbol", "fwd_ret"])


def price_columns(price: str) -> set[str]:
    """bars columns a收益口径 needs — feeds the engine's panel projection
    (WP3.1: cols = factor.required_columns ∪ price_columns(cfg.return_price)).
    vwap keeps close alongside (its documented fallback); open_next enters on
    open and exits on close.
    """
    if price == "vwap":
        return {"vwap", "close"}
    if price == "open_next":
        return {"open", "close"}
    return {"close"}
