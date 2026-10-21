#!/usr/bin/env python3
"""第5步：组合回测（第5、6、7章总装）.

流程：加载分位数面板与打分 -> 波动率映射（训练期拟合、样本外冻结）
     -> 拥挤度三重信号 -> 风险覆盖后的打分 -> 两遍式风控回测
     -> 对冲叠加 -> 输出表7.5全套绩效。

用法：
  python3 scripts/05_run_backtest.py --config configs/backtest_hs300.yaml
  python3 scripts/05_run_backtest.py --config configs/backtest_csi800.yaml
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from kbarfm.backtest.run import run_two_pass, save_results
from kbarfm.data.qlib_source import (
    get_pool_instruments,
    init_qlib,
    load_ohlcva,
)
from kbarfm.factor.signal import apply_risk_overlays
from kbarfm.risk.crowding import crowding_panel
from kbarfm.risk.volatility import (
    apply_width_calibration,
    fit_width_calibration,
    realized_vol_panel,
    vol_breadth_series,
)
from kbarfm.utils.config import load_config
from kbarfm.utils.logging import get_logger

logger = get_logger("kbarfm.script.backtest")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/backtest_hs300.yaml")
    args = parser.parse_args()
    cfg = load_config(args.config)
    rk = dict(cfg.risk)

    init_qlib(cfg.provider_uri, cfg.region)

    # ---- 因子与分位数面板 ----
    panel = pd.read_parquet(cfg.factor_panel)
    score = pd.read_pickle(cfg.signal_path)

    # ---- 前瞻波动率（5.1）：训练期拟合宽度->已实现波动率映射，样本外冻结 ----
    calib = pd.read_parquet(cfg.calib_panel)
    instruments = get_pool_instruments(cfg.pool, cfg.start, cfg.end)
    ohlcva_calib = load_ohlcva(
        instruments,
        str(calib.index.get_level_values("datetime").min().date()),
        str(calib.index.get_level_values("datetime").max().date()),
        freq="day",
    )
    close_calib = ohlcva_calib["close"].unstack(level="instrument")
    rv = realized_vol_panel(close_calib, horizon=10).stack()
    rv.index.names = ["datetime", "instrument"]
    knots = fit_width_calibration(calib["width"], rv)
    vol_pred = apply_width_calibration(panel["width"], knots)

    # ---- 拥挤度三重信号（5.2，表5.2）----
    ohlcva = load_ohlcva(instruments, cfg.start, cfg.end, freq="day")
    close = ohlcva["close"].unstack(level="instrument")
    volume = ohlcva["volume"].unstack(level="instrument")
    amount = ohlcva["amount"].unstack(level="instrument")
    crowded = crowding_panel(
        close, volume, amount,
        min_signals=rk["crowding_min_signals"],
        turnover_z=rk["turnover_z"], turnover_q=rk["turnover_q"],
        pv_corr=rk["pv_corr"], pv_band_pos=rk["pv_band_pos"],
        conc_q=rk["conc_q"],
    )

    # ---- 风险覆盖后的最终打分（6.1.1：因子定方向、波动率定幅度）----
    score_overlaid = apply_risk_overlays(
        score, vol_pred=vol_pred, crowded=crowded,
        vol_downweight_q=rk["vol_downweight_q"],
        vol_exclude_q=rk["vol_exclude_q"],
        downweight_scale=rk["vol_downweight_scale"],
    )

    # ---- 对冲条件的广度序列（表5.4二级条件②）----
    vb = vol_breadth_series(vol_pred, top_q=rk["vol_downweight_q"])

    # ---- 两遍式风控回测 + 对冲叠加 ----
    result = run_two_pass(cfg, score_overlaid, vol_breadth=vb, crowd_share=None)

    save_results(result, cfg.output_dir)
    logger.info("==== 绩效汇总（表7.5口径）====")
    for k, v in result["summary"].items():
        logger.info("%s: %s", k, f"{v:.4f}" if isinstance(v, float) else v)


if __name__ == "__main__":
    main()
