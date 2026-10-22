"""量价预测因子的样本外单因子检验（7.3）.

  7.3.1 IC与RankIC检验：逐日横截面(秩)相关、ICIR、
        Newey-West修正t统计量（自研实现）、正RankIC占比；
  7.3.2 因子分层组合检验：每个调仓截面按因子分值等分10组（G1最低、
        G10最高）构建等权组合，统计相对池等权基准的年化超额收益；
  7.3.3 因子信息衰减：RankIC随前瞻期H的变化。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from qlib.data import D

from ..data.qlib_source import get_pool_instruments
from .metrics import TRADING_DAYS


def forward_returns(
    pool: str, start: str, end: str, horizon: int
) -> pd.Series:
    """未来horizon日简单收益，index=(datetime, instrument)."""
    instruments = get_pool_instruments(pool, start, end)
    expr = f"Ref($close, -{horizon}) / $close - 1"
    df = D.features(instruments, [expr], start_time=start, end_time=end, freq="day")
    s = df.iloc[:, 0]
    s.index = s.index.swaplevel()
    return s.sort_index().rename(f"fwd{horizon}")


def daily_ic(
    score: pd.Series, fwd: pd.Series, method: str = "pearson"
) -> pd.Series:
    df = pd.concat([score.rename("s"), fwd.rename("r")], axis=1).dropna()
    return df.groupby(level="datetime").apply(lambda g: g["s"].corr(g["r"], method=method))


def newey_west_tstat(x: pd.Series, lags: int | None = None) -> float:
    """IC序列均值的Newey-West修正t统计量（自研，Bartlett核）."""
    v = x.dropna().to_numpy(dtype=np.float64)
    T = len(v)
    if T < 10:
        return np.nan
    if lags is None:
        lags = int(np.floor(4 * (T / 100.0) ** (2.0 / 9.0)))
    e = v - v.mean()
    gamma0 = float(e @ e) / T
    var = gamma0
    for j in range(1, lags + 1):
        gamma_j = float(e[j:] @ e[:-j]) / T
        var += 2.0 * (1.0 - j / (lags + 1.0)) * gamma_j
    se = np.sqrt(var / T)
    return float(v.mean() / se) if se > 0 else np.nan


def ic_summary(score: pd.Series, fwd: pd.Series) -> dict:
    """表7.4口径的汇总统计."""
    ic = daily_ic(score, fwd, "pearson")
    ric = daily_ic(score, fwd, "spearman")
    return {
        "IC均值": float(ic.mean()),
        "RankIC均值": float(ric.mean()),
        "ICIR(RankIC口径)": float(ric.mean() / ric.std()) if ric.std() > 0 else np.nan,
        "t统计量(Newey-West)": newey_west_tstat(ric),
        "正RankIC占比": float((ric > 0).mean()),
        "样本天数": int(len(ric)),
    }


def decile_test(
    score: pd.Series,
    fwd_rebal: pd.Series,
    rebalance_freq: int = 5,
    n_groups: int = 10,
) -> pd.DataFrame:
    """分层组合检验（7.3.2，图7.4口径）.

    fwd_rebal: 与调仓周期对齐的未来rebalance_freq日收益。
    每个调仓截面等分n_groups组，等权持有至下一调仓日，
    输出各组年化收益与相对池等权基准的年化超额。
    """
    df = pd.concat([score.rename("s"), fwd_rebal.rename("r")], axis=1).dropna()
    dates = df.index.get_level_values("datetime").unique().sort_values()
    rebal_dates = dates[::rebalance_freq]
    df = df[df.index.get_level_values("datetime").isin(rebal_dates)]

    def _grouped(g: pd.DataFrame) -> pd.Series:
        q = pd.qcut(g["s"].rank(method="first"), n_groups, labels=False)
        return g.groupby(q)["r"].mean()

    period = df.groupby(level="datetime").apply(_grouped).unstack()  # date × group
    bench = df.groupby(level="datetime")["r"].mean()                  # 池等权基准

    periods_per_year = TRADING_DAYS / rebalance_freq
    out = []
    for g in range(n_groups):
        nav = (1.0 + period[g].dropna()).prod()
        ann = nav ** (periods_per_year / max(len(period[g].dropna()), 1)) - 1.0
        bnav = (1.0 + bench.reindex(period[g].dropna().index)).prod()
        bann = bnav ** (periods_per_year / max(len(period[g].dropna()), 1)) - 1.0
        out.append({"group": f"G{g + 1}", "年化收益": ann, "年化超额": ann - bann})
    return pd.DataFrame(out).set_index("group")


def decay_profile(
    score: pd.Series, pool: str, start: str, end: str,
    horizons: tuple[int, ...] = (1, 2, 3, 5, 10, 15, 20),
) -> pd.DataFrame:
    """因子信息衰减特性（7.3.3，图7.5口径）：RankIC均值随前瞻期变化."""
    rows = []
    for h in horizons:
        fwd = forward_returns(pool, start, end, h)
        ric = daily_ic(score, fwd, "spearman")
        rows.append({"horizon": h, "RankIC均值": float(ric.mean())})
    return pd.DataFrame(rows).set_index("horizon")
