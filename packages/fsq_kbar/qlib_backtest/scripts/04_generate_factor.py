#!/usr/bin/env python3
"""第4步：生成量价预测因子（4.2.3 / 4.2.4）.

对样本外区间与波动率校准区间各生成一份分位数面板，
再合成横截面标准化打分。

用法：
  python3 scripts/04_generate_factor.py --config configs/factor.yaml --pool csi300
  python3 scripts/04_generate_factor.py --config configs/factor.yaml --pool csi800
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from kbarfm.factor.inference import generate_quantile_panel
from kbarfm.factor.signal import build_score
from kbarfm.utils.config import load_config
from kbarfm.utils.logging import get_logger

logger = get_logger("kbarfm.script.factor")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/factor.yaml")
    parser.add_argument("--pool", default=None, help="覆盖配置中的标的池")
    args = parser.parse_args()

    cfg = load_config(args.config)
    pool = args.pool or cfg.pool
    out_dir = Path(cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 样本外分位数面板（回测用）
    panel = generate_quantile_panel(
        cfg, pool, cfg.start, cfg.end,
        out_dir / f"quantile_panel_{pool}.parquet",
    )
    # 训练期内校准面板（波动率映射拟合用，样本外冻结，5.1）
    generate_quantile_panel(
        cfg, pool, cfg.calib_start, cfg.calib_end,
        out_dir / f"quantile_panel_calib_{pool}.parquet",
    )

    # 风险调整 + 横截面标准化 -> 选股打分
    score = build_score(
        panel,
        quantile_pair=tuple(cfg.quantile_pair),
        epsilon=cfg.epsilon,
    )
    score_path = out_dir / f"score_{pool}.pkl"
    score.to_pickle(score_path)
    logger.info("因子打分 %s -> %s", score.shape, score_path)


if __name__ == "__main__":
    main()
