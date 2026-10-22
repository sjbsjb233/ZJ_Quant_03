#!/usr/bin/env python3
"""步骤6：因子样本外检验（报告7.3，表7.4 / 图7.4 / 图7.5）.

IC/RankIC统计（含Newey-West t）、十分位分层组合、信息衰减曲线。

用法：
    python scripts/06_eval_factor.py --config configs/factor.yaml
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kbarfm.eval.decay import ic_decay
from kbarfm.eval.ic import ic_summary
from kbarfm.eval.layering import layered_returns
from kbarfm.utils.config import load_config
from kbarfm.utils.logging import get_logger

log = get_logger("scripts.eval_factor")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/factor.yaml")
    args = ap.parse_args()
    cfg = load_config(args.config)
    out_dir = Path(cfg.out_dir)

    factor_long = pd.read_parquet(out_dir / "factor_panel.parquet")
    factor = factor_long["factor"].unstack("symbol")          # date x symbol

    prices_long = pd.read_parquet(cfg.panel_path)
    prices_long["date"] = pd.to_datetime(prices_long["date"])
    prices = prices_long.pivot_table(index="date", columns="symbol", values="close")
    prices = prices.reindex(factor.index.union(prices.index)).sort_index()

    horizon = cfg.get_path("horizon", 10)

    # ---- IC / RankIC（表7.4）----
    summary = ic_summary(factor, prices, horizon=horizon)
    log.info(
        "样本外因子检验 | IC均值=%.4f RankIC均值=%.4f ICIR=%.3f "
        "NW-t=%.2f 正RankIC占比=%.1f%%",
        summary["ic_mean"], summary["rank_ic_mean"], summary["icir_rank"],
        summary["t_stat_nw"], summary["positive_rank_ic_share"] * 100,
    )
    summary["rank_ic_series"].to_frame("rank_ic").to_parquet(out_dir / "rank_ic_series.parquet")

    # ---- 分层组合（图7.4）----
    layers = layered_returns(factor, prices, n_groups=10,
                             rebalance_every=cfg.get_path("rebalance_every", 5))
    log.info("分层组合年化超额:\n%s", layers.round(4).to_string())
    log.info("多空价差=%.1f%% 单调性=%s",
             layers.attrs["long_short_spread"] * 100, layers.attrs["monotonic"])
    layers.to_parquet(out_dir / "layered_returns.parquet")

    # ---- 信息衰减（图7.5）----
    decay = ic_decay(factor, prices)
    log.info("信息衰减曲线:\n%s", decay.round(4).to_string())
    decay.to_parquet(out_dir / "ic_decay.parquet")


if __name__ == "__main__":
    main()
