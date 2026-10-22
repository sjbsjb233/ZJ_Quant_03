"""FSQ K-line foundation-model factors (engine: packages/fsq_kbar).

The FSQ engine tokenises daily K-lines with Finite Scalar Quantization, runs a
decoder-only Transformer and emits next-10-day return quantiles
(q0.1 / q0.5 / q0.9) in one deterministic forward pass. Training and inference
need a GPU + PyTorch, so they run offline in the engine's own container; its
``scripts/04_generate_factor.py`` writes ``factor_panel.parquet``:

    index  (date, symbol)
    alpha_raw  q0.5 / (q0.9 - q0.1 + 1e-3)          risk-adjusted signal
    factor     1%/99% winsorised + cross-sectional z-score of alpha_raw
    width      q0.9 - q0.1                            forward-risk proxy

These builtins surface that panel inside the platform, so FSQ goes through the
same evaluation pipeline (preprocess / neutralise / IC-IR / layering /
correlation) as every hand-crafted factor. They import no torch: the factor
service stays light and the engine can be retrained without redeploying it.

Panel location: $FSQ_FACTOR_PANEL, else $FACTOR_STORE_ROOT/fsq/factor_panel.parquet.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import polars as pl

from ..base import Factor, FactorContext
from ..registry import register

_QLIB_CODE = re.compile(r"^(SH|SZ|BJ)(\d{6})$")


def fsq_panel_path() -> Path:
    explicit = os.environ.get("FSQ_FACTOR_PANEL")
    if explicit:
        return Path(explicit)
    root = os.environ.get("FACTOR_STORE_ROOT", "./factor-store")
    return Path(root) / "fsq" / "factor_panel.parquet"


def _to_platform_symbol(code: str) -> str:
    """'SH600519' (Qlib) / '600519' / '600519.SH' -> '600519.SH'."""
    from quant_data.symbology import normalize_symbol

    s = str(code).strip().upper()
    m = _QLIB_CODE.match(s)
    if m:
        return f"{m.group(2)}.{m.group(1)}"
    return normalize_symbol(s)


def read_fsq_column(ctx: FactorContext, column: str) -> pl.DataFrame:
    """One column of the FSQ panel as [date, symbol, value] on ctx's window."""
    path = fsq_panel_path()
    if not path.exists():
        raise FileNotFoundError(
            f"FSQ factor panel not found at {path}. Run the FSQ engine "
            "(packages/fsq_kbar: scripts/04_generate_factor.py) and point "
            "FSQ_FACTOR_PANEL at its factor_panel.parquet."
        )
    panel = pl.read_parquet(path, columns=["date", "symbol", column])
    symbols = pl.Series([_to_platform_symbol(s) for s in panel["symbol"]])
    return (
        panel.with_columns(
            pl.col("date").cast(pl.Date),
            symbols.alias("symbol"),
            pl.col(column).cast(pl.Float64).alias("value"),
        )
        .filter(
            pl.col("symbol").is_in(ctx.symbols)
            & pl.col("date").is_between(ctx.start, ctx.end)
        )
        .select(["date", "symbol", "value"])
        .drop_nulls("value")
    )


@register
class FSQAlpha(Factor):
    """Core FSQ signal: median forecast scaled by forecast confidence.

    Same median return, wider q0.1–q0.9 band -> the model is less sure -> the
    signal is compressed. Ranked top-k long-only in the FSQ strategy.
    """

    category = "ml"
    direction = 1
    requires_pit = False
    dependencies = ("raw.bars_daily", "fsq.factor_panel")
    required_columns = ("close",)          # bars only feed forward returns
    param_schema = {}
    display_name = "FSQ 风险调整预测因子"
    description = (
        "FSQ K-line foundation model: z(q0.5 / (q0.9 - q0.1 + 1e-3)), "
        "90-day lookback, H=10 forecast horizon"
    )
    tags = ("fsq", "foundation-model", "price-volume")

    def compute(self, ctx: FactorContext) -> pl.DataFrame:
        return read_fsq_column(ctx, "factor")


@register
class FSQWidth(Factor):
    """Forecast uncertainty: width of the q0.1–q0.9 return band.

    The FSQ risk layer maps it (isotonic calibration, frozen out of sample)
    to forward realised volatility and down-weights the top 20% cross-section.
    """

    category = "vol"
    direction = -1
    requires_pit = False
    dependencies = ("raw.bars_daily", "fsq.factor_panel")
    required_columns = ("close",)
    param_schema = {}
    display_name = "FSQ 预测区间宽度"
    description = "FSQ K-line foundation model: q0.9 - q0.1 of the 10-day return forecast"
    tags = ("fsq", "foundation-model", "risk")

    def compute(self, ctx: FactorContext) -> pl.DataFrame:
        return read_fsq_column(ctx, "width")
