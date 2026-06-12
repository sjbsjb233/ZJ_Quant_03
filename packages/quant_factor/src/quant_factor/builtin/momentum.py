"""Momentum factors.

  - Mom : N-day momentum (price[t-1]/price[t-window-1] - 1), skip 1 day to
          avoid microstructure bias.
  - Reversal : negative N-day reversal — short-term anti-momentum.
"""
from __future__ import annotations

import polars as pl

from ..base import Factor, FactorContext
from ..registry import register


@register
class Mom(Factor):
    category = "momentum"
    direction = 1
    requires_pit = False
    dependencies = ("raw.bars_daily",)
    required_columns = ("close",)          # 内存治理 WP3.1: 面板列投影
    param_schema = {
        "window": (int, 20),
        "skip": (int, 1),
    }
    display_name = "N 日动量"
    description = "Price[t-skip] / Price[t-skip-window] - 1, post-adjusted close"
    tags = ("momentum",)

    def compute(self, ctx: FactorContext) -> pl.DataFrame:
        w = self.params["window"]
        s = self.params["skip"]
        bars = ctx.bars(adjust="post").sort(["symbol", "date"])
        return (
            bars.with_columns(
                (
                    pl.col("close").shift(s).over("symbol")
                    / pl.col("close").shift(s + w).over("symbol")
                    - 1.0
                ).alias("value")
            )
            .select(["date", "symbol", "value"])
            .drop_nulls("value")
        )


@register
class Reversal(Factor):
    category = "momentum"
    direction = -1
    requires_pit = False
    dependencies = ("raw.bars_daily",)
    required_columns = ("close",)          # 内存治理 WP3.1
    param_schema = {"window": (int, 20)}
    display_name = "N 日反转"
    description = "Negative short-window momentum — reversal of recent moves"
    tags = ("momentum",)

    def compute(self, ctx: FactorContext) -> pl.DataFrame:
        w = self.params["window"]
        bars = ctx.bars(adjust="post").sort(["symbol", "date"])
        return (
            bars.with_columns(
                (
                    -(
                        pl.col("close") / pl.col("close").shift(w).over("symbol") - 1.0
                    )
                ).alias("value")
            )
            .select(["date", "symbol", "value"])
            .drop_nulls("value")
        )


@register
class TurnoverN(Factor):
    """N-day average turnover (volume / float_share if available, else volume)."""

    category = "flow"
    direction = -1  # high turnover historically associated with weaker returns
    requires_pit = False
    dependencies = ("raw.bars_daily",)
    # float_share 可能未入库 — 读取层会自动剔缺列,本因子按列缺失走 volume 兜底
    required_columns = ("volume", "float_share")   # 内存治理 WP3.1
    param_schema = {"window": (int, 20)}
    display_name = "N 日换手率"
    description = "rolling mean of volume / float_share over N days"
    tags = ("flow",)

    def compute(self, ctx: FactorContext) -> pl.DataFrame:
        w = self.params["window"]
        bars = ctx.bars(adjust="post").sort(["symbol", "date"])
        if "float_share" in bars.columns:
            bars = bars.with_columns(
                pl.when(pl.col("float_share") > 0)
                .then(pl.col("volume") / pl.col("float_share"))
                .otherwise(None)
                .alias("_to")
            )
        else:
            bars = bars.with_columns(pl.col("volume").alias("_to"))
        return (
            bars.with_columns(
                pl.col("_to").rolling_mean(w).over("symbol").alias("value")
            )
            .select(["date", "symbol", "value"])
            .drop_nulls("value")
        )
