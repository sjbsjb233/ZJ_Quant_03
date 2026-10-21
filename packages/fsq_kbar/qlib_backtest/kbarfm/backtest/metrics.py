"""绩效评估指标体系（7.2.2，表7.3 / 表7.5口径）.

AER为策略与基准年化收益之差（几何年化）；IR为单位跟踪误差的超额
收益；夏普按无风险利率2%；月度超额按日度超额算术加总统计。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def _annualized(cum_ret: float, n_days: int) -> float:
    return (1.0 + cum_ret) ** (TRADING_DAYS / max(n_days, 1)) - 1.0


def max_drawdown(nav: pd.Series) -> float:
    return float((nav / nav.cummax() - 1.0).min())


def performance_summary(report: pd.DataFrame, risk_free: float = 0.02) -> dict:
    """report: Qlib日频回测报告，需含 'return'（组合日收益，已扣成本）
    与 'bench'（基准日收益）两列。输出表7.5全部指标。
    """
    r = report["return"].dropna()
    b = report["bench"].reindex(r.index).fillna(0.0)
    n = len(r)

    nav_s = (1.0 + r).cumprod()
    nav_b = (1.0 + b).cumprod()
    cum_s = float(nav_s.iloc[-1] - 1.0)
    cum_b = float(nav_b.iloc[-1] - 1.0)
    ann_s = _annualized(cum_s, n)
    ann_b = _annualized(cum_b, n)
    aer = ann_s - ann_b                                   # 几何年化口径

    excess = r - b
    ir = float(excess.mean() / excess.std() * np.sqrt(TRADING_DAYS)) if excess.std() > 0 else np.nan
    sharpe = float((ann_s - risk_free) / (r.std() * np.sqrt(TRADING_DAYS))) if r.std() > 0 else np.nan
    mdd = max_drawdown(nav_s)
    calmar = float(ann_s / abs(mdd)) if mdd < 0 else np.nan

    # 月度胜率：日度超额按月算术加总（表7.3注）
    monthly = excess.groupby(pd.Grouper(freq="ME")).sum()
    win_rate = float((monthly > 0).mean()) if len(monthly) else np.nan

    return {
        "回测区间": f"{r.index[0]:%Y-%m-%d} 至 {r.index[-1]:%Y-%m-%d}",
        "策略累计绝对收益": cum_s,
        "策略年化绝对收益": ann_s,
        "基准累计收益": cum_b,
        "基准年化收益": ann_b,
        "年化超额收益AER": aer,
        "信息比率IR": ir,
        "夏普比率": sharpe,
        "最大回撤": mdd,
        "Calmar比率": calmar,
        "月度胜率": win_rate,
        "交易日数": n,
    }


def monthly_excess_table(report: pd.DataFrame) -> pd.DataFrame:
    """月度超额收益（图7.9热力图数据源）：行=年份，列=月份."""
    excess = (report["return"] - report["bench"]).dropna()
    m = excess.groupby(pd.Grouper(freq="ME")).sum()
    return pd.DataFrame(
        {"year": m.index.year, "month": m.index.month, "excess": m.to_numpy()}
    ).pivot(index="year", columns="month", values="excess")


def turnover_summary(report: pd.DataFrame) -> dict:
    """样本外换手统计（图6.2口径）."""
    if "turnover" not in report:
        return {}
    to = report["turnover"].dropna()
    active = to[to > 1e-6]
    return {
        "调仓日单边换手均值": float(active.mean() / 2) if len(active) else np.nan,
        "全区间日均换手": float(to.mean()),
    }
