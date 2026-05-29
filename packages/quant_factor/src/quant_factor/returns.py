"""Forward returns — strict t -> t+h, no look-ahead.

Operates on post-adjusted prices (continuous through corporate actions).
Returns a DataFrame [date, symbol, fwd_ret] where on date t the value is the
price ratio price[t+h] / price[t] - 1, computed per symbol via Polars'
.shift(-h).over("symbol"). Rows near the edge become NaN.
"""
from __future__ import annotations

import datetime as dt

import polars as pl


def forward_returns(
    reader,
    symbols: list[str],
    start: dt.date,
    end: dt.date,
    horizon: int,
    price: str = "close",
) -> pl.DataFrame:
    """Compute (date, symbol, fwd_ret) for horizon h.

    `price`:
      - "close"     : per-day adj close (post)
      - "vwap"      : per-day adj vwap (post)
      - "open_next" : approximates "buy at next open, hold h-1 days"; we use
                      open shift(-1) -> close shift(-h). Close-fallback if
                      open is missing for some bars.
    """
    bars = pl.from_arrow(
        reader.read_bars_daily(symbols=symbols, start=start, end=end, adjust="post")
    )
    if bars.height == 0:
        return pl.DataFrame(
            {"date": [], "symbol": [], "fwd_ret": []},
            schema={"date": pl.Date, "symbol": pl.Utf8, "fwd_ret": pl.Float64},
        )
    bars = bars.sort(["symbol", "date"])
    if price == "vwap" and "vwap" in bars.columns:
        col = "vwap"
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
