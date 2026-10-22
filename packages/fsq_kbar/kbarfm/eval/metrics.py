"""组合绩效评估指标体系（报告7.2.2，表7.3）.

因子质量、组合收益与风险控制三层指标：年化收益、AER（几何年化
超额）、信息比率、夏普比率（无风险利率2%）、最大回撤、Calmar、
月度胜率（日度超额算术加总的月度口径）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 250
RISK_FREE = 0.02


def _annualize(nav: pd.Series) -> float:
    n = len(nav)
    if n < 2 or nav.iloc[0] <= 0:
        return float("nan")
    return float((nav.iloc[-1] / nav.iloc[0]) ** (TRADING_DAYS / (n - 1)) - 1.0)


def max_drawdown(nav: pd.Series) -> float:
    peak = nav.cummax()
    return float((nav / peak - 1.0).min())


def nav_metrics(nav: pd.Series, bench_nav: pd.Series) -> dict:
    """nav / bench_nav: DatetimeIndex 净值序列（同一区间）."""
    nav = nav.dropna()
    bench_nav = bench_nav.reindex(nav.index).dropna()
    nav = nav.reindex(bench_nav.index)

    ret = nav.pct_change().dropna()
    bench_ret = bench_nav.pct_change().dropna()
    excess_ret = (ret - bench_ret).dropna()

    ann = _annualize(nav)
    bench_ann = _annualize(bench_nav)
    # AER：策略与基准年化收益之差（几何年化口径，表7.3注）
    aer = ann - bench_ann

    vol = float(ret.std() * np.sqrt(TRADING_DAYS))
    tracking_error = float(excess_ret.std() * np.sqrt(TRADING_DAYS))
    ir = aer / tracking_error if tracking_error > 0 else float("nan")
    sharpe = (ann - RISK_FREE) / vol if vol > 0 else float("nan")

    mdd = max_drawdown(nav)
    calmar = ann / abs(mdd) if mdd < 0 else float("nan")

    # 月度胜率：日度超额算术加总的月度口径（表7.3注）
    monthly_excess = excess_ret.groupby(pd.Grouper(freq="ME")).sum()
    win_rate = float((monthly_excess > 0).mean()) if len(monthly_excess) else float("nan")

    return {
        "cum_return": float(nav.iloc[-1] / nav.iloc[0] - 1.0),
        "ann_return": ann,
        "bench_cum_return": float(bench_nav.iloc[-1] / bench_nav.iloc[0] - 1.0),
        "bench_ann_return": bench_ann,
        "aer": aer,
        "information_ratio": ir,
        "sharpe": sharpe,
        "volatility": vol,
        "tracking_error": tracking_error,
        "max_drawdown": mdd,
        "calmar": calmar,
        "monthly_win_rate": win_rate,
        "n_days": int(len(nav)),
    }
