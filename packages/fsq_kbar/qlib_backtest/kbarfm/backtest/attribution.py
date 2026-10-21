"""绩效归因分析（7.5）.

  7.5.1 超额收益来源分解：模块化消融——依次叠加换手控制、波动率
        风控、回撤警戒/择时对冲，相邻两档AER之差即该模块贡献；
  7.5.2 分市况适应性：按图7.2(b)四段市况分段统计（表7.7口径）；
  7.5.3 分行业超额贡献：持仓权重 × 个股相对基准超额收益，
        按申万一级行业聚合（图7.13口径）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..data.qlib_source import load_industry_map
from ..utils.logging import get_logger
from .ic_analysis import forward_returns
from .metrics import TRADING_DAYS, max_drawdown, performance_summary

logger = get_logger("kbarfm.attribution")


# ---------------------------------------------------------------- 7.5.2
def regime_table(report: pd.DataFrame, regimes: dict) -> pd.DataFrame:
    """分市况绩效对比（表7.7口径）."""
    rows = []
    for name, (s, e) in regimes.items():
        seg = report.loc[pd.Timestamp(s) : pd.Timestamp(e)]
        if seg.empty:
            continue
        r, b = seg["return"].fillna(0.0), seg["bench"].fillna(0.0)
        nav = (1.0 + r).cumprod()
        cum_s = float(nav.iloc[-1] - 1.0)
        cum_b = float((1.0 + b).prod() - 1.0)
        n = len(seg)
        ann_s = (1.0 + cum_s) ** (TRADING_DAYS / n) - 1.0
        ann_b = (1.0 + cum_b) ** (TRADING_DAYS / n) - 1.0
        rows.append(
            {
                "市况阶段": name,
                "区间": f"{seg.index[0]:%Y-%m} ~ {seg.index[-1]:%Y-%m}",
                "策略区间收益": cum_s,
                "基准区间收益": cum_b,
                "年化超额收益AER": ann_s - ann_b,
                "段内最大回撤": max_drawdown(nav),
            }
        )
    return pd.DataFrame(rows).set_index("市况阶段")


# ---------------------------------------------------------------- 7.5.3
def _positions_to_weights(positions: dict) -> pd.Series:
    """Qlib持仓字典 -> 长表权重Series，index=(datetime, instrument)."""
    rows = {}
    for date, pos in positions.items():
        try:
            wd = pos.get_stock_weight_dict(only_stock=True)
        except Exception:
            continue
        for inst, w in wd.items():
            rows[(pd.Timestamp(date), inst)] = float(w)
    s = pd.Series(rows, name="weight")
    s.index.names = ["datetime", "instrument"]
    return s.sort_index()


def industry_contribution(
    positions: dict,
    report: pd.DataFrame,
    pool: str,
    start: str,
    end: str,
    industry_map_path: str,
) -> pd.DataFrame:
    """分行业超额贡献（年化百分点，图7.13口径）.

    日度贡献 = 行业内持仓权重 × (个股次日收益 - 基准次日收益)，
    对全区间加总后年化。
    """
    weights = _positions_to_weights(positions)
    stock_ret = forward_returns(pool, start, end, horizon=1)
    bench_ret = report["bench"].reindex(
        weights.index.get_level_values("datetime")
    ).to_numpy()

    df = weights.to_frame().join(stock_ret.rename("ret"), how="left")
    df["excess"] = (df["ret"].to_numpy() - bench_ret) * df["weight"].to_numpy()

    ind = load_industry_map(industry_map_path)
    df["industry"] = [
        ind.get(i, "其他") for i in df.index.get_level_values("instrument")
    ]
    total = df.groupby("industry")["excess"].sum()
    n_days = report["return"].dropna().shape[0]
    annualized = total * (TRADING_DAYS / max(n_days, 1))
    return (
        annualized.sort_values(ascending=False)
        .rename("年化超额贡献")
        .to_frame()
    )


# ---------------------------------------------------------------- 7.5.1
def module_decomposition(cfg, signal_raw: pd.DataFrame, signal_overlaid: pd.DataFrame,
                         run_backtest_fn, run_two_pass_fn) -> pd.DataFrame:
    """超额收益来源分解（图7.10口径）：递进式消融.

      A 基线：原始因子打分，逐日调仓、无换手约束；
      B = A + 换手与成本控制（drop-n、5日调仓、最短持有期）；
      C = B + 波动率风险预算与拥挤剔除（打分覆盖）；
      D = C + 回撤警戒降仓 + 择时对冲（两遍式全风控）。
    相邻档位AER之差即对应模块贡献；D为最终口径17.8%/13.5%的来源。
    """
    p = dict(cfg.portfolio)
    results = {}

    rep_a, _ = run_backtest_fn(
        cfg, signal_raw,
        portfolio_overrides={"rebalance_freq": 1, "n_drop": p["topk"], "hold_thresh": 1},
    )
    results["A_因子多头选股"] = performance_summary(rep_a, cfg.risk_free)

    rep_b, _ = run_backtest_fn(cfg, signal_raw)
    results["B_+换手与成本控制"] = performance_summary(rep_b, cfg.risk_free)

    rep_c, _ = run_backtest_fn(cfg, signal_overlaid)
    results["C_+波动率风控"] = performance_summary(rep_c, cfg.risk_free)

    full = run_two_pass_fn(cfg, signal_overlaid)
    rep_d = full["report"].copy()
    rep_d["return"] = rep_d["hedged_return"].fillna(rep_d["return"])
    results["D_+回撤警戒与择时对冲"] = performance_summary(rep_d, cfg.risk_free)

    table = pd.DataFrame(results).T
    table["模块增量贡献"] = table["年化超额收益AER"].diff()
    table.loc["A_因子多头选股", "模块增量贡献"] = table.loc[
        "A_因子多头选股", "年化超额收益AER"
    ]
    return table
