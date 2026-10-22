"""量价拥挤多维监控（5.2，表5.2）.

三重拥挤信号，任意两项同时触发即判定该标的处于拥挤状态：
  ① 换手异常：当日换手（以成交量相对活跃度代理）相对过去20日均值
     的偏离超2倍60日标准差，或处于60日滚动85%分位以上；
  ② 量价背离：过去20日收盘价与成交量的滚动相关系数低于-0.3，
     且价格处于20日区间上沿；
  ③ 资金集中：个股成交额占全池总成交额比例处于60日滚动90%分位以上。
监控信号仅在风险管理层面发挥"刹车"作用，不参与核心选股打分。
"""

from __future__ import annotations

import pandas as pd


def turnover_anomaly(
    volume: pd.DataFrame,
    mean_window: int = 20,
    std_window: int = 60,
    z_thresh: float = 2.0,
    q_window: int = 60,
    q_thresh: float = 0.85,
) -> pd.DataFrame:
    """①换手异常（宽表 datetime × instrument -> bool）."""
    dev = volume - volume.rolling(mean_window).mean()
    z = dev / volume.rolling(std_window).std()
    rolling_q = volume.rolling(q_window).rank(pct=True)
    return (z > z_thresh) | (rolling_q > q_thresh)


def price_volume_divergence(
    close: pd.DataFrame,
    volume: pd.DataFrame,
    corr_window: int = 20,
    corr_thresh: float = -0.30,
    band_pos: float = 0.80,
) -> pd.DataFrame:
    """②量价背离（量价顶背离的经典判定形式）."""
    corr = close.rolling(corr_window).corr(volume)
    lo = close.rolling(corr_window).min()
    hi = close.rolling(corr_window).max()
    pos = (close - lo) / (hi - lo).where(hi > lo)
    return (corr < corr_thresh) & (pos >= band_pos)


def capital_concentration(
    amount: pd.DataFrame, q_window: int = 60, q_thresh: float = 0.90
) -> pd.DataFrame:
    """③资金集中：成交额占比的60日滚动分位."""
    share = amount.div(amount.sum(axis=1), axis=0)
    rolling_q = share.rolling(q_window).rank(pct=True)
    return rolling_q > q_thresh


def crowding_panel(
    close: pd.DataFrame,
    volume: pd.DataFrame,
    amount: pd.DataFrame,
    min_signals: int = 2,
    **kwargs,
) -> pd.Series:
    """综合拥挤判定 -> 长表bool Series，index=(datetime, instrument)."""
    s1 = turnover_anomaly(
        volume,
        z_thresh=kwargs.get("turnover_z", 2.0),
        q_thresh=kwargs.get("turnover_q", 0.85),
    )
    s2 = price_volume_divergence(
        close, volume,
        corr_thresh=kwargs.get("pv_corr", -0.30),
        band_pos=kwargs.get("pv_band_pos", 0.80),
    )
    s3 = capital_concentration(amount, q_thresh=kwargs.get("conc_q", 0.90))

    count = s1.astype(int) + s2.astype(int) + s3.astype(int)
    crowded = (count >= min_signals).stack()
    crowded.index.names = ["datetime", "instrument"]
    return crowded.rename("crowded")


def crowded_holding_share(
    crowded: pd.Series, holdings: pd.Series
) -> pd.Series:
    """拥挤标的占持仓比例（表5.4二级条件③）.

    holdings: 长表持仓权重，index=(datetime, instrument)。
    """
    df = pd.concat(
        [crowded.rename("crowded"), holdings.rename("w")], axis=1
    ).dropna(subset=["w"])
    df["crowded"] = df["crowded"].fillna(False).astype(bool)
    share = df.groupby(level="datetime").apply(
        lambda g: g.loc[g["crowded"], "w"].sum() / max(g["w"].sum(), 1e-12)
    )
    return share.rename("crowd_share")
