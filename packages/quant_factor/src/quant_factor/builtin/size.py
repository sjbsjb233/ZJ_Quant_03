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
            # safe fallback to total_mv if circ_mv missing — but record the口径
            # change (设计 §6) instead of silently swapping market-cap meaning.
            alt = "total_mv" if field == "circ_mv" else "circ_mv"
            if alt not in bars.columns:
                # cannot compute — return empty
                return pl.DataFrame(
                    schema={"date": pl.Date, "symbol": pl.Utf8, "value": pl.Float64}
                )
            ledger = getattr(ctx, "ledger", None)
            if ledger is not None and not ledger.has_code("DG_FIELD_FALLBACK"):
                from ..degradation import make_event

                ledger.record(
                    make_event(
                        "DG_FIELD_FALLBACK",
                        stage="compute",
                        reason=f"市值字段 {field} 缺失,LogMktCap 回退使用 {alt}",
                        impact=f"因子口径由 {field} 变为 {alt},与配置不一致",
                        suggested_action=f"补齐 raw.bars_daily.{field} 后重跑",
                        config_key="field",
                        evidence={"requested": field, "used": alt},
                    )
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
