#!/usr/bin/env python3
"""步骤4：生成量价预测因子并导出SuperMind信号文件（报告4.2.4）.

流程：
  1. 读取标的池日频行情面板（parquet：date x symbol 的 OHLCVA长表）；
  2. 全池滚动分位数推理（90日回看、H=10、确定性单次前向）；
  3. 因子构建（风险调整 + 缩尾 + 截面z-score）；
  4. 区间宽度经波动率校准器映射为预测波动率；
  5. 导出：
     - factor_panel.parquet（研究/回测用）
     - kbarfm_signal_data.py（上传SuperMind"我的研究"根目录，
       供执行端策略一级信号源读取）

行情长表格式：columns = [date, symbol, open, high, low, close, volume, amount]

用法：
    python scripts/04_generate_factor.py --config configs/factor.yaml
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kbarfm.data.panel import load_panel
from kbarfm.factor.inference import FactorEngine
from kbarfm.factor.signal import build_factor_frame
from kbarfm.factor.volatility import VolatilityCalibrator
from kbarfm.model.io import load_foundation, load_tokenizer
from kbarfm.utils.config import load_config
from kbarfm.utils.logging import get_logger

log = get_logger("scripts.generate_factor")


def export_signal_module(factor_df: pd.DataFrame, vol_series: pd.Series, out_path: Path) -> None:
    """导出 kbarfm_signal_data.py：SIGNALS[date][symbol] = (factor, vol_pred)."""
    lines = [
        "# -*- coding: utf-8 -*-",
        '"""kbarfm预计算信号文件（由scripts/04_generate_factor.py自动生成）.',
        "",
        "SIGNALS[date_str][symbol] = (factor_zscore, predicted_volatility)",
        '"""',
        "",
        "SIGNALS = {",
    ]
    for date, group in factor_df.groupby(level="date"):
        date_str = pd.Timestamp(date).strftime("%Y-%m-%d")
        lines.append(f"    '{date_str}': {{")
        for (_, symbol), row in group.iterrows():
            vol = vol_series.get((date, symbol), float("nan"))
            lines.append(
                f"        '{symbol}': ({row['factor']:.6f}, {vol:.6f}),"
            )
        lines.append("    },")
    lines.append("}")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("SuperMind信号文件已导出: %s（%d个交易日）",
             out_path, factor_df.index.get_level_values("date").nunique())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/factor.yaml")
    ap.add_argument("--start", default=None, help="覆盖配置的起始日期")
    ap.add_argument("--end", default=None, help="覆盖配置的结束日期")
    args = ap.parse_args()
    cfg = load_config(args.config)

    tokenizer = load_tokenizer(cfg.tokenizer_ckpt)
    model = load_foundation(cfg.model_ckpt)
    engine = FactorEngine(
        tokenizer, model,
        lookback=cfg.get_path("lookback", 90),
        batch_size=cfg.get_path("batch_size", 256),
    )

    panel = load_panel(cfg.panel_path)
    log.info("行情面板载入完成：%d只标的", len(panel))

    all_dates = sorted(set().union(*[set(df.index) for df in panel.values()]))
    start = pd.Timestamp(args.start or cfg.get_path("start", "2025-07-01"))
    end = pd.Timestamp(args.end or cfg.get_path("end", "2026-06-18"))
    dates = [d for d in all_dates if start <= d <= end]
    log.info("推理区间: %s ~ %s（%d个交易日）", dates[0].date(), dates[-1].date(), len(dates))

    quantile_panel = engine.quantile_panel(panel, dates)
    factor_df = build_factor_frame(
        quantile_panel,
        eps=cfg.get_path("eps", 1e-3),
        winsor=tuple(cfg.get_path("winsor", (0.01, 0.99))),
    )

    out_dir = Path(cfg.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    factor_df.to_parquet(out_dir / "factor_panel.parquet")
    log.info("因子面板已写出: %s", out_dir / "factor_panel.parquet")

    # 区间宽度 -> 预测波动率（校准器由步骤5拟合；未拟合时导出原始宽度）
    calib_path = Path(cfg.get_path("vol_calibrator", str(out_dir / "vol_calibrator.parquet")))
    if calib_path.exists():
        calib = VolatilityCalibrator.from_frame(pd.read_parquet(calib_path))
        vol_series = pd.Series(
            calib.predict(factor_df["width"].to_numpy()), index=factor_df.index
        )
        log.info("已按冻结的单调映射输出预测波动率（5.1节口径）")
    else:
        vol_series = factor_df["width"]
        log.warning("未找到波动率校准器（%s），信号文件中输出原始区间宽度（序数等价）", calib_path)

    export_signal_module(factor_df, vol_series, out_dir / "kbarfm_signal_data.py")


if __name__ == "__main__":
    main()
