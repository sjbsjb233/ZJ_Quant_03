"""多维风险监控（报告5.2，表5.2）.

量价拥挤三重信号（任意两项同时触发即判定拥挤）：
  换手异常：当日换手相对过去20日均值的偏离超2倍60日标准差，
           或处于60日滚动85%分位以上；
  量价背离：过去20日收盘价与成交量滚动相关系数低于-0.3，
           且价格处于20日区间上沿；
  资金集中：个股成交额占全市场（或行业）总成交额比例处于
           60日滚动90%分位以上。

因子有效性监控：20日滚动RankIC均值低于0.02且连续5个交易日
判定因子暂时失效（暂停新建仓位、防御模式）；回升至0.03以上且
连续5个交易日解除。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


class CrowdingMonitor:
    # 表5.2阈值
    TURNOVER_SIGMA = 2.0
    TURNOVER_QUANTILE = 0.85
    PV_CORR_THRESH = -0.3
    PV_PRICE_POS = 0.90          # 收盘价位于20日区间上沿（区间位置>=0.9）
    CONC_QUANTILE = 0.90
    CORR_WINDOW = 20
    STAT_WINDOW = 60

    def turnover_anomaly(self, turnover: pd.Series) -> bool:
        """turnover: 至少60+20日的换手（或成交量代理）序列，末位为当日."""
        s = turnover.dropna()
        if len(s) < self.STAT_WINDOW + 1:
            return False
        today = float(s.iloc[-1])
        mean20 = float(s.iloc[-21:-1].mean())
        std60 = float(s.iloc[-self.STAT_WINDOW - 1 : -1].std(ddof=1))
        cond_sigma = std60 > 0 and abs(today - mean20) > self.TURNOVER_SIGMA * std60
        rank = float((s.iloc[-self.STAT_WINDOW :] <= today).mean())
        cond_quantile = rank >= self.TURNOVER_QUANTILE
        return bool(cond_sigma or cond_quantile)

    def pv_divergence(self, close: pd.Series, volume: pd.Series) -> bool:
        c = close.dropna().iloc[-self.CORR_WINDOW :]
        v = volume.reindex(c.index).fillna(0.0)
        if len(c) < self.CORR_WINDOW:
            return False
        corr = float(np.corrcoef(c.to_numpy(), v.to_numpy())[0, 1]) if v.std() > 0 else 0.0
        lo, hi = float(c.min()), float(c.max())
        pos = (float(c.iloc[-1]) - lo) / (hi - lo) if hi > lo else 0.5
        return bool(corr < self.PV_CORR_THRESH and pos >= self.PV_PRICE_POS)

    def concentration(self, amount: pd.Series, market_amount: pd.Series) -> bool:
        share = (amount / market_amount.replace(0.0, np.nan)).dropna()
        if len(share) < self.STAT_WINDOW:
            return False
        s = share.iloc[-self.STAT_WINDOW :]
        rank = float((s <= s.iloc[-1]).mean())
        return bool(rank >= self.CONC_QUANTILE)

    def is_crowded(
        self,
        close: pd.Series,
        volume: pd.Series,
        turnover: pd.Series | None = None,
        amount: pd.Series | None = None,
        market_amount: pd.Series | None = None,
    ) -> tuple[bool, dict]:
        """三重信号汇总：任意两项同时触发即判定拥挤（表5.2注）."""
        flags = {
            "turnover_anomaly": self.turnover_anomaly(
                turnover if turnover is not None else volume
            ),
            "pv_divergence": self.pv_divergence(close, volume),
            "concentration": (
                self.concentration(amount, market_amount)
                if amount is not None and market_amount is not None
                else False
            ),
        }
        return sum(flags.values()) >= 2, flags


def rank_ic(scores: pd.Series, forward_returns: pd.Series) -> float:
    """单截面RankIC：因子打分与未来收益的秩相关系数."""
    df = pd.concat([scores, forward_returns], axis=1, keys=["s", "r"]).dropna()
    if len(df) < 5:
        return float("nan")
    rs = df["s"].rank()
    rr = df["r"].rank()
    return float(np.corrcoef(rs.to_numpy(), rr.to_numpy())[0, 1])


class FactorICMonitor:
    """因子有效性状态机（表5.2 信号失效行）."""

    FAIL_THRESH = 0.02
    RECOVER_THRESH = 0.03
    ROLLING_WINDOW = 20
    CONSECUTIVE_DAYS = 5

    def __init__(self) -> None:
        self.ic_history: list[float] = []
        self.defensive = False
        self._below_count = 0
        self._above_count = 0

    def update(self, daily_rank_ic: float) -> bool:
        """录入当日RankIC，返回当前是否处于防御模式."""
        if np.isfinite(daily_rank_ic):
            self.ic_history.append(float(daily_rank_ic))
        if len(self.ic_history) < self.ROLLING_WINDOW:
            return self.defensive

        rolling_mean = float(np.mean(self.ic_history[-self.ROLLING_WINDOW :]))
        if not self.defensive:
            self._below_count = self._below_count + 1 if rolling_mean < self.FAIL_THRESH else 0
            if self._below_count >= self.CONSECUTIVE_DAYS:
                self.defensive = True                  # 暂停新建仓位，人工复核
                self._above_count = 0
        else:
            self._above_count = self._above_count + 1 if rolling_mean > self.RECOVER_THRESH else 0
            if self._above_count >= self.CONSECUTIVE_DAYS:
                self.defensive = False                 # 恢复常态调仓
                self._below_count = 0
        return self.defensive

    @property
    def rolling_ic(self) -> float:
        if len(self.ic_history) < self.ROLLING_WINDOW:
            return float("nan")
        return float(np.mean(self.ic_history[-self.ROLLING_WINDOW :]))
