#!/usr/bin/env python3
"""步骤5：波动率校准器拟合（报告5.1，表5.1）.

在训练期区间上，以分位区间宽度 (q0.9 - q0.1) 对未来H日已实现
波动率做保序回归拟合单调映射；样本外冻结该映射。

用法：
    python scripts/05_calibrate_vol.py --config configs/factor.yaml \
        --train-start 2024-01-01 --train-end 2025-06-30
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kbarfm.data.panel import load_panel
from kbarfm.factor.inference import FactorEngine
from kbarfm.factor.volatility import VolatilityCalibrator, realized_volatility
from kbarfm.model.io import load_foundation, load_tokenizer
from kbarfm.utils.config import load_config
from kbarfm.utils.logging import get_logger

log = get_logger("scripts.calibrate_vol")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/factor.yaml")
    ap.add_argument("--train-start", default="2024-01-01")
    ap.add_argument("--train-end", default="2025-06-30", help="须不晚于预训练语料截止日")
    args = ap.parse_args()
    cfg = load_config(args.config)

    tokenizer = load_tokenizer(cfg.tokenizer_ckpt)
    model = load_foundation(cfg.model_ckpt)
    engine = FactorEngine(tokenizer, model, lookback=cfg.get_path("lookback", 90))

    panel = load_panel(cfg.panel_path)
    horizon = cfg.get_path("horizon", 10)

    all_dates = sorted(set().union(*[set(df.index) for df in panel.values()]))
    start, end = pd.Timestamp(args.train_start), pd.Timestamp(args.train_end)
    dates = [d for d in all_dates if start <= d <= end]
    # 校准无需逐日全密度，隔5日采样即可获得充分样本
    dates = dates[::5]
    log.info("训练期校准采样: %d个截面", len(dates))

    quantile_panel = engine.quantile_panel(panel, dates)
    widths, realized = [], []
    rv_cache = {s: realized_volatility(df["close"], horizon) for s, df in panel.items()}
    for (date, symbol), row in quantile_panel.iterrows():
        rv = rv_cache.get(symbol)
        if rv is None or date not in rv.index:
            continue
        r = rv.loc[date]
        if pd.notna(r):
            widths.append(row["q90"] - row["q10"])
            realized.append(float(r))

    calib = VolatilityCalibrator().fit(pd.Series(widths), pd.Series(realized))
    metrics = calib.evaluate(pd.Series(widths), pd.Series(realized))
    log.info("训练期拟合完成（表5.1口径）: MAE=%.4f R2=%.3f n=%d",
             metrics["mae"], metrics["r2"], metrics["n"])

    out_dir = Path(cfg.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    calib.to_frame().to_parquet(out_dir / "vol_calibrator.parquet")
    log.info("校准器已冻结保存: %s", out_dir / "vol_calibrator.parquet")


if __name__ == "__main__":
    main()
