"""Size factors — ln(market_cap) variants.

LogMktCap is the canonical "small-minus-big" raw factor; lower is better in
Chinese A-share historically (small-cap premium).
"""
from __future__ import annotations

import polars as pl

from ..base import Factor, FactorContext
from ..registry import register


@register
class LogMktCap(Factor):
    category = "size"
    direction = -1
    requires_pit = False
    dependencies = ("raw.bars_daily",)
    param_schema = {"field": (str, "circ_mv")}  # or "total_mv"
    display_name = "对数市值"
    description = "ln(circ_mv) — small-minus-big size factor"
    tags = ("size",)

    def compute(self, ctx: FactorContext) -> pl.DataFrame:
        field = self.params["field"]
        if field not in ("circ_mv", "total_mv"):
            field = "circ_mv"
        bars = ctx.bars(adjust="none")
        if field not in bars.columns:
            # safe fallback to total_mv if circ_mv missing
            alt = "total_mv" if field == "circ_mv" else "circ_mv"
            if alt not in bars.columns:
                # cannot compute — return empty
                return pl.DataFrame(
                    schema={"date": pl.Date, "symbol": pl.Utf8, "value": pl.Float64}
                )
            field = alt
        return (
            bars.with_columns(
                pl.when(pl.col(field) > 0)
                .then(pl.col(field).log())
                .otherwise(None)
                .alias("value")
            )
            .select(["date", "symbol", "value"])
            .drop_nulls("value")
        )
