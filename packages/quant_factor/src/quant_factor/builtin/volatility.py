"""Volatility factors — rolling stdev of daily returns."""
from __future__ import annotations

import polars as pl

from ..base import Factor, FactorContext
from ..registry import register


@register
class Volatility(Factor):
    category = "vol"
    direction = -1
    requires_pit = False
    dependencies = ("raw.bars_daily",)
    required_columns = ("close",)          # 内存治理 WP3.1
    param_schema = {"window": (int, 60)}
    display_name = "N 日波动率"
    description = "rolling stdev of daily log returns over N days"
    tags = ("vol",)

    def compute(self, ctx: FactorContext) -> pl.DataFrame:
        w = self.params["window"]
        bars = ctx.bars(adjust="post").sort(["symbol", "date"])
        return (
            bars.with_columns(
                pl.col("close").pct_change().over("symbol").alias("_ret")
            )
            .with_columns(
                pl.col("_ret").rolling_std(w).over("symbol").alias("value")
            )
            .select(["date", "symbol", "value"])
            .drop_nulls("value")
        )
