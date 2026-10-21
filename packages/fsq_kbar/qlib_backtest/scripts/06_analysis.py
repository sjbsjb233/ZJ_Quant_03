#!/usr/bin/env python3
"""第6步：单因子检验、归因与稳健性分析（7.3 / 7.5 / 7.6）.

用法：
  python3 scripts/06_analysis.py --config configs/backtest_hs300.yaml
  python3 scripts/06_analysis.py --config configs/backtest_csi800.yaml --sensitivity --decompose
"""

import argparse
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from kbarfm.backtest.attribution import (
    industry_contribution,
    module_decomposition,
    regime_table,
)
from kbarfm.backtest.ic_analysis import (
    daily_ic,
    decay_profile,
    decile_test,
    forward_returns,
    ic_summary,
)
from kbarfm.backtest.metrics import monthly_excess_table, turnover_summary
from kbarfm.backtest.run import run_backtest, run_two_pass
from kbarfm.backtest.sensitivity import (
    cost_sensitivity,
    param_grid_sensitivity,
    quantile_pair_sensitivity,
)
from kbarfm.data.qlib_source import init_qlib
from kbarfm.utils.config import load_config
from kbarfm.utils.logging import get_logger

logger = get_logger("kbarfm.script.analysis")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/backtest_hs300.yaml")
    parser.add_argument("--sensitivity", action="store_true", help="7.6 敏感性检验（需重跑多次回测）")
    parser.add_argument("--decompose", action="store_true", help="7.5.1 模块化归因分解")
    args = parser.parse_args()

    cfg = load_config(args.config)
    init_qlib(cfg.provider_uri, cfg.region)
    out = Path(cfg.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    score = pd.read_pickle(cfg.signal_path)["score"]
    panel = pd.read_parquet(cfg.factor_panel)
    report = pd.read_parquet(out / "report.parquet")

    # ---- 7.3.1 IC与RankIC检验（表7.4）----
    fwd10 = forward_returns(cfg.pool, cfg.start, cfg.end, horizon=10)
    summary = ic_summary(score, fwd10)
    pd.Series(summary).to_csv(out / "ic_summary.csv", header=False)
    logger.info("IC/RankIC汇总: %s", summary)

    ric_daily = daily_ic(score, fwd10, "spearman")
    pd.DataFrame(
        {"RankIC": ric_daily, "RankIC_20d": ric_daily.rolling(20).mean(),
         "RankIC_cum": ric_daily.cumsum()}
    ).to_csv(out / "rankic_series.csv")          # 图7.3数据源

    # ---- 7.3.2 分层组合检验（图7.4）----
    fwd5 = forward_returns(cfg.pool, cfg.start, cfg.end, horizon=cfg.portfolio.rebalance_freq)
    deciles = decile_test(score, fwd5, rebalance_freq=cfg.portfolio.rebalance_freq)
    deciles.to_csv(out / "decile_test.csv")
    logger.info("分层检验:\n%s", deciles)

    # ---- 7.3.3 信息衰减（图7.5）----
    decay = decay_profile(score, cfg.pool, cfg.start, cfg.end)
    decay.to_csv(out / "decay_profile.csv")

    # ---- 7.4.3 月度超额分布（图7.9）与换手统计（图6.2）----
    monthly_excess_table(report).to_csv(out / "monthly_excess.csv")
    pd.Series(turnover_summary(report)).to_csv(out / "turnover_summary.csv", header=False)

    # ---- 7.5.2 分市况（表7.7）----
    regimes = {k: tuple(v) for k, v in dict(cfg.regimes).items()}
    regime_table(report, regimes).to_csv(out / "regime_table.csv")

    # ---- 7.5.3 分行业贡献（图7.13）----
    pos_path = out / "positions.pkl"
    if pos_path.exists():
        with open(pos_path, "rb") as f:
            positions = pickle.load(f)
        industry_contribution(
            positions, report, cfg.pool, cfg.start, cfg.end, cfg.industry_map
        ).to_csv(out / "industry_contribution.csv")

    # ---- 7.5.1 模块化归因分解（图7.10）----
    if args.decompose:
        raw_score = pd.read_pickle(cfg.signal_path)      # 未覆盖版（近似基线）
        decomp = module_decomposition(
            cfg, raw_score, raw_score, run_backtest, run_two_pass
        )
        decomp.to_csv(out / "module_decomposition.csv")
        logger.info("归因分解:\n%s", decomp)

    # ---- 7.6 敏感性 ----
    if args.sensitivity:
        signal = pd.read_pickle(cfg.signal_path)
        cost = cost_sensitivity(cfg, signal, run_backtest)
        cost.to_csv(out / "cost_sensitivity.csv")
        logger.info("盈亏平衡单边成本 ~ %.2f%%", cost.attrs["盈亏平衡单边成本"] * 100)

        grid = param_grid_sensitivity(cfg, signal, run_backtest)
        grid.to_csv(out / "param_grid_sensitivity.csv")

        qp = quantile_pair_sensitivity(panel, cfg.pool, cfg.start, cfg.end)
        qp.to_csv(out / "quantile_pair_sensitivity.csv")

    logger.info("分析结果全部输出至 %s", out)


if __name__ == "__main__":
    main()
