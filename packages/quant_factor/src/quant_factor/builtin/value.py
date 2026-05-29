"""Value factors (PIT, fundamentals-based).

  - BookToPrice (BP) : book_value_per_share_t / price_t — high BP = cheap.
                       Uses fundamentals.bps with publish_date PIT filter.
"""
from __future__ import annotations

import datetime as dt

import polars as pl

from ..base import Factor, FactorContext
from ..registry import register


@register
class BookToPrice(Factor):
    category = "value"
    direction = 1
    requires_pit = True
    dependencies = ("raw.bars_daily", "raw.fundamentals")
    param_schema = {}
    display_name = "账面市值比 (BP)"
    description = "Most-recently-published bps / today's close — PIT"
    tags = ("value",)

    def compute_cross_section(
        self, ctx: FactorContext, as_of: dt.date
    ) -> pl.DataFrame:
        fund = ctx.fundamentals(as_of)
        if fund.height == 0 or "bps" not in fund.columns:
            return pl.DataFrame(
                schema={"symbol": pl.Utf8, "value": pl.Float64}
            )
        # Take latest publish per symbol.
        latest = (
            fund.sort(["symbol", "publish_date"], descending=[False, True])
            .group_by("symbol")
            .first()
            .select(["symbol", "bps"])
        )
        # Today's close (use post-adjusted to be consistent).
        bars = ctx.bars(adjust="none")
        today = bars.filter(pl.col("date") == as_of).select(["symbol", "close"])
        return (
            latest.join(today, on="symbol", how="inner")
            .with_columns(
                pl.when(pl.col("close") > 0)
                .then(pl.col("bps") / pl.col("close"))
                .otherwise(None)
                .alias("value")
            )
            .select(["symbol", "value"])
            .drop_nulls("value")
        )
