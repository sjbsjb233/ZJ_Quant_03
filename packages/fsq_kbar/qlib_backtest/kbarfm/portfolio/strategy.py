"""仓位管理策略：Qlib TopkDropout 扩展（第6章）.

在Qlib标准 TopkDropoutStrategy（top-k等权多头 + drop-n换手约束 +
hold_thresh最短持有期 + only_tradable可交易性复核）之上扩展：

  ① 调仓节拍：每 rebalance_freq（5）个交易日执行一次调仓，
     非调仓日返回空决策（6.2.1）；警戒层级变动日强制允许交易，
     以便按表5.3及时降仓（force_dates）；
  ② 动态风险度：get_risk_degree 按日查询回撤警戒状态机输出的
     目标仓位序列（表5.3 / 表6.2：常态现金缓冲3% -> 0.97，
     二级降仓50%-70% -> 0.60，三级20%-30% -> 0.25）；
  ③ 防御模式：一级警戒/因子失效日暂停新建仓位，返回空决策，
     仅由风险处置通道（force_dates）执行降仓（6.2.2）。

波动率风险预算与拥挤剔除已前置到打分（factor/signal.py的
apply_risk_overlays），被剔除标的打分为NaN、不参与top-k排序，
实现"因子定方向、波动率定幅度"的协同（6.1.1）。
"""

from __future__ import annotations

import pandas as pd

from qlib.backtest.decision import TradeDecisionWO
from qlib.contrib.strategy import TopkDropoutStrategy


class KBarTopkStrategy(TopkDropoutStrategy):
    def __init__(
        self,
        *,
        rebalance_freq: int = 5,
        default_risk_degree: float = 0.97,
        risk_degree_series: pd.Series | None = None,
        freeze_dates: set | None = None,
        force_dates: set | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.rebalance_freq = int(rebalance_freq)
        self.default_risk_degree = float(default_risk_degree)
        self.risk_degree_series = risk_degree_series
        self.freeze_dates = {pd.Timestamp(d).normalize() for d in (freeze_dates or set())}
        self.force_dates = {pd.Timestamp(d).normalize() for d in (force_dates or set())}

    # ---- 当前交易日 ----
    def _current_date(self) -> pd.Timestamp:
        step = self.trade_calendar.get_trade_step()
        return pd.Timestamp(self.trade_calendar.get_step_time(step)[0]).normalize()

    # ---- ② 动态风险度（表5.3 / 表6.2）----
    def get_risk_degree(self, trade_step=None) -> float:
        if self.risk_degree_series is None or len(self.risk_degree_series) == 0:
            return self.default_risk_degree
        try:
            step = (
                trade_step
                if trade_step is not None
                else self.trade_calendar.get_trade_step()
            )
            t = pd.Timestamp(self.trade_calendar.get_step_time(step)[0])
            s = self.risk_degree_series.sort_index()
            pos = s.index.searchsorted(t, side="right") - 1
            if pos >= 0:
                return float(s.iloc[pos])
        except Exception:
            pass
        return self.default_risk_degree

    # ---- ①③ 调仓节拍与防御模式 ----
    def generate_trade_decision(self, execute_result=None):
        step = self.trade_calendar.get_trade_step()
        date = self._current_date()
        forced = date in self.force_dates

        # 非调仓日：不生成交易（drop-n与最短持有期由父类在调仓日执行）
        if step % self.rebalance_freq != 0 and not forced:
            return TradeDecisionWO([], self)

        # 防御/一级警戒日：暂停新建仓位；仅警戒变动日的强制降仓放行
        if date in self.freeze_dates and not forced:
            return TradeDecisionWO([], self)

        return super().generate_trade_decision(execute_result)
