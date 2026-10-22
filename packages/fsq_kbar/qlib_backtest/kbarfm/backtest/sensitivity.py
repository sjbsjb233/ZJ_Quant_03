"""稳健性与敏感性检验（7.6）.

  7.6.1 交易成本敏感性：单边成本0.05%→0.50%扫描AER，并给出
        盈亏平衡成本的线性外推估计（图7.14口径）；
  7.6.2 组合构建参数敏感性：持仓数k × 调仓频率 网格（图7.15口径）；
  7.6.3 因子构成与分位点敏感性：风险调整分母的分位点对
        (q0.05,q0.95) / (q0.1,q0.9) / (q0.25,q0.75) 及仅取q0.5的
        对照，以RankIC比较（图7.16口径；需以扩展分位数头
        [0.05,0.1,0.25,0.5,0.75,0.9,0.95] 生成的面板为输入）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..factor.signal import build_score, cross_sectional_zscore
from ..utils.logging import get_logger
from .ic_analysis import daily_ic, forward_returns
from .metrics import performance_summary

logger = get_logger("kbarfm.sensitivity")


def cost_sensitivity(
    cfg, signal: pd.DataFrame, run_backtest_fn,
    costs: tuple[float, ...] = (0.0005, 0.001, 0.0015, 0.002, 0.003, 0.004, 0.005),
) -> pd.DataFrame:
    """7.6.1 交易成本敏感性."""
    rows = []
    for c in costs:
        report, _ = run_backtest_fn(
            cfg, signal, portfolio_overrides={"open_cost": c, "close_cost": c}
        )
        s = performance_summary(report, cfg.risk_free)
        rows.append({"单边成本": c, "AER": s["年化超额收益AER"], "IR": s["信息比率IR"]})
    df = pd.DataFrame(rows).set_index("单边成本")

    # 盈亏平衡成本：AER对成本近似线性，最小二乘外推至AER=0
    x, y = df.index.to_numpy(), df["AER"].to_numpy()
    slope, intercept = np.polyfit(x, y, 1)
    df.attrs["盈亏平衡单边成本"] = float(-intercept / slope) if slope < 0 else np.inf
    return df


def param_grid_sensitivity(
    cfg, signal: pd.DataFrame, run_backtest_fn,
    topk_list: tuple[int, ...] | None = None,
    freq_list: tuple[int, ...] = (1, 3, 5, 10, 20),
) -> pd.DataFrame:
    """7.6.2 持仓数k × 调仓频率 的AER热力表."""
    base_k = cfg.portfolio.topk
    if topk_list is None:
        topk_list = tuple(int(base_k * r) for r in (0.5, 0.8, 1.0, 1.2, 2.0))
    rows = []
    for k in topk_list:
        for f in freq_list:
            report, _ = run_backtest_fn(
                cfg, signal,
                portfolio_overrides={
                    "topk": k,
                    "n_drop": max(1, k // 10),   # drop-n随k同比例缩放（n/k上限一致）
                    "rebalance_freq": f,
                },
            )
            s = performance_summary(report, cfg.risk_free)
            rows.append({"topk": k, "rebalance_freq": f, "AER": s["年化超额收益AER"]})
    return pd.DataFrame(rows).pivot(index="topk", columns="rebalance_freq", values="AER")


def quantile_pair_sensitivity(
    panel: pd.DataFrame, pool: str, start: str, end: str, horizon: int = 10,
    epsilon: float = 1e-3,
) -> pd.DataFrame:
    """7.6.3 因子构成敏感性：分位点对与是否风险调整的RankIC对比."""
    fwd = forward_returns(pool, start, end, horizon)
    variants: dict[str, pd.Series] = {}

    for lo, hi in ((0.05, 0.95), (0.10, 0.90), (0.25, 0.75)):
        lo_c = f"q{int(lo * 100):02d}"
        hi_c = f"q{int(hi * 100):02d}"
        if lo_c not in panel.columns or hi_c not in panel.columns:
            logger.warning("面板缺少 %s/%s（需扩展分位数头生成），跳过", lo_c, hi_c)
            continue
        score = build_score(panel, quantile_pair=(lo, hi), epsilon=epsilon)["score"]
        variants[f"风险调整({lo_c},{hi_c})"] = score

    if "q50" in panel.columns:  # 仅中位数、不做风险调整的对照
        variants["仅中位数q50"] = cross_sectional_zscore(panel["q50"])

    rows = []
    for name, score in variants.items():
        ric = daily_ic(score, fwd, "spearman")
        rows.append({"因子构成": name, "RankIC均值": float(ric.mean())})
    return pd.DataFrame(rows).set_index("因子构成")
