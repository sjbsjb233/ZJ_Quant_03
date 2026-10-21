"""IC / RankIC检验（报告7.3.1，表7.4）.

IC：因子打分与未来实际收益的截面线性相关；
RankIC：二者的秩相关，对异常值更稳健、更贴合按排序分组的选股用法；
ICIR = IC均值 / IC标准差；t统计量经Newey-West修正。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _forward_return(prices: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """prices: date x symbol 收盘价面板 -> 未来horizon日累计收益."""
    return prices.shift(-horizon) / prices - 1.0


def ic_series(
    factor: pd.DataFrame,
    prices: pd.DataFrame,
    horizon: int = 10,
    method: str = "spearman",
) -> pd.Series:
    """逐日IC序列.

    factor: date x symbol 因子面板；prices: date x symbol 收盘价面板。
    method: "pearson"（IC）或 "spearman"（RankIC）。
    """
    fwd = _forward_return(prices, horizon)
    out = {}
    for date in factor.index:
        if date not in fwd.index:
            continue
        pair = pd.concat([factor.loc[date], fwd.loc[date]], axis=1, keys=["f", "r"]).dropna()
        if len(pair) < 20:
            continue
        if method == "spearman":
            corr = pair["f"].rank().corr(pair["r"].rank())
        else:
            corr = pair["f"].corr(pair["r"])
        out[date] = float(corr)
    return pd.Series(out).sort_index()


def newey_west_tstat(series: pd.Series, lags: int = 10) -> float:
    """IC均值显著性的Newey-West修正t统计量."""
    x = series.dropna().to_numpy(dtype=np.float64)
    n = len(x)
    if n < lags + 2:
        return float("nan")
    mean = x.mean()
    e = x - mean
    gamma0 = float(np.dot(e, e)) / n
    var = gamma0
    for lag in range(1, lags + 1):
        w = 1.0 - lag / (lags + 1.0)
        gamma = float(np.dot(e[lag:], e[:-lag])) / n
        var += 2.0 * w * gamma
    se = np.sqrt(var / n)
    return float(mean / se) if se > 0 else float("nan")


def ic_summary(
    factor: pd.DataFrame,
    prices: pd.DataFrame,
    horizon: int = 10,
    rolling_window: int = 20,
) -> dict:
    """两池汇总统计（表7.4口径）."""
    ic = ic_series(factor, prices, horizon, method="pearson")
    ric = ic_series(factor, prices, horizon, method="spearman")
    return {
        "ic_mean": float(ic.mean()),
        "rank_ic_mean": float(ric.mean()),
        "icir_rank": float(ric.mean() / ric.std()) if ric.std() > 0 else float("nan"),
        "t_stat_nw": newey_west_tstat(ric),
        "positive_rank_ic_share": float((ric > 0).mean()),
        "rolling_rank_ic": ric.rolling(rolling_window).mean(),
        "rank_ic_series": ric,
        "ic_series": ic,
    }
