"""风险对冲机制：常态零对冲 + 分级动态对冲（报告5.4，表5.4）.

对冲比例阶梯：
  常态                0%   无任何警戒信号，保留完整Beta与Alpha
  一级警戒（观察）     20%  回撤触及一级警戒（DD>=5%）
  单一二级条件         60%  以下任一：①DD>=8%；②标的池内超30%成分股
                          处于横截面波动率前20%分位；③拥挤标的占持仓
                          比例超20%
  多重条件共振         80%  上述二级条件中任两项及以上同时触发
  完全对冲            100% 回撤触及三级警戒（DD>=12%）
  解除：相应条件解除且连续3个交易日后回落；三级解除后按周逐级回补。

对冲工具：股指期货（IF/IC）空头；
对冲张数 = 组合市值 × 对冲比例 ÷（合约乘数 × 期货价格）。
"""

from __future__ import annotations

from .drawdown import AlertLevel

# 中金所股指期货合约乘数（元/点）
CONTRACT_MULTIPLIER = {"IF": 300, "IC": 200, "IH": 300}

VOL_ALERT_POOL_SHARE = 0.30      # 二级条件②：池内高波动成分股占比阈值
CROWDED_HOLDING_SHARE = 0.20     # 二级条件③：拥挤标的持仓占比阈值
RELEASE_CONSECUTIVE_DAYS = 3


class HedgeLadder:
    """对冲比例状态机（表5.4）."""

    LADDER = (0.0, 0.20, 0.60, 0.80, 1.00)

    def __init__(self) -> None:
        self.ratio = 0.0
        self._release_count = 0

    def target_ratio(
        self,
        dd_level: AlertLevel,
        vol_alert_share: float,
        crowded_share: float,
    ) -> float:
        """由当前风险信号计算目标对冲比例（未含解除延迟）."""
        if dd_level >= AlertLevel.L3_DEFENSE:
            return 1.00
        l2_conditions = [
            dd_level >= AlertLevel.L2_DERISK,
            vol_alert_share > VOL_ALERT_POOL_SHARE,
            crowded_share > CROWDED_HOLDING_SHARE,
        ]
        n_l2 = sum(l2_conditions)
        if n_l2 >= 2:
            return 0.80
        if n_l2 == 1:
            return 0.60
        if dd_level >= AlertLevel.L1_WATCH:
            return 0.20
        return 0.0

    def update(
        self,
        dd_level: AlertLevel,
        vol_alert_share: float,
        crowded_share: float,
    ) -> float:
        """升级立即生效；降级需连续3个交易日条件解除后逐级回落."""
        target = self.target_ratio(dd_level, vol_alert_share, crowded_share)
        if target >= self.ratio:
            self.ratio = target
            self._release_count = 0
        else:
            self._release_count += 1
            if self._release_count >= RELEASE_CONSECUTIVE_DAYS:
                # 逐级回落（不一步清零，与"按周逐级回补"精神一致）
                idx = max(0, self.LADDER.index(self._nearest(self.ratio)) - 1)
                self.ratio = max(self.LADDER[idx], target)
                self._release_count = 0
        return self.ratio

    @classmethod
    def _nearest(cls, r: float) -> float:
        return min(cls.LADDER, key=lambda x: abs(x - r))


def hedge_contracts(
    portfolio_value: float,
    hedge_ratio: float,
    futures_price: float,
    contract: str = "IF",
) -> int:
    """对冲张数 = 组合市值 × 对冲比例 ÷（合约乘数 × 期货价格）."""
    if futures_price <= 0 or hedge_ratio <= 0:
        return 0
    mult = CONTRACT_MULTIPLIER.get(contract.upper(), 300)
    return int(round(portfolio_value * hedge_ratio / (mult * futures_price)))
