"""组合回撤三级警戒机制（报告5.2，表5.3）.

  一级警戒（观察）    5% <= DD < 8%   暂停新建仓位，仅执行存量必要调仓
  二级警戒（降仓+对冲）8% <= DD < 12%  整体降仓至原目标权重的50%-70%
  三级警戒（全面防守） DD >= 12%       降仓至20%-30%（或空仓），完全对冲
  警戒解除：DD自低点修复至8%以内且波动率预测回落至正常区间，
  连续3个交易日满足后，按周度逐级回补仓位并退出对冲。
"""

from __future__ import annotations

from enum import IntEnum


class AlertLevel(IntEnum):
    NORMAL = 0
    L1_WATCH = 1
    L2_DERISK = 2
    L3_DEFENSE = 3


# 各警戒级别的权益目标仓位比例（相对原目标权重）
EQUITY_SCALE = {
    AlertLevel.NORMAL: 1.00,
    AlertLevel.L1_WATCH: 1.00,       # 一级只暂停新建仓位，存量不动
    AlertLevel.L2_DERISK: 0.60,      # 表5.3：50%-70%取中值
    AlertLevel.L3_DEFENSE: 0.25,     # 表5.3：20%-30%取中值
}

DD_L1, DD_L2, DD_L3 = 0.05, 0.08, 0.12
RELEASE_DD = 0.08
RELEASE_CONSECUTIVE_DAYS = 3
REFILL_STEP_DAYS = 5                 # 解除后按周度（5个交易日）逐级回补


class DrawdownGuard:
    """回撤警戒状态机：输入每日净值，输出警戒级别与权益仓位系数."""

    def __init__(self) -> None:
        self.peak_nav: float | None = None
        self.level = AlertLevel.NORMAL
        self.drawdown = 0.0
        self._release_count = 0
        self._refill_days = 0        # 解除后处于回补期的天数
        self._refill_from: AlertLevel = AlertLevel.NORMAL

    def update(self, nav: float, vol_normal: bool = True) -> AlertLevel:
        """nav: 组合当日净值（总资产）；vol_normal: 波动率预测是否回落正常."""
        if self.peak_nav is None or nav > self.peak_nav:
            self.peak_nav = nav
        self.drawdown = 1.0 - nav / self.peak_nav if self.peak_nav > 0 else 0.0
        dd = self.drawdown

        # 升级：回撤触及更高警戒线立即升级
        target = AlertLevel.NORMAL
        if dd >= DD_L3:
            target = AlertLevel.L3_DEFENSE
        elif dd >= DD_L2:
            target = AlertLevel.L2_DERISK
        elif dd >= DD_L1:
            target = AlertLevel.L1_WATCH

        if target > self.level:
            self.level = target
            self._release_count = 0
            self._refill_days = 0
            return self.level

        # 解除：DD修复至8%以内且波动率正常，连续3个交易日
        if self.level >= AlertLevel.L2_DERISK:
            if dd < RELEASE_DD and vol_normal:
                self._release_count += 1
            else:
                self._release_count = 0
            if self._release_count >= RELEASE_CONSECUTIVE_DAYS:
                self._refill_from = self.level
                self.level = AlertLevel.L1_WATCH if dd >= DD_L1 else AlertLevel.NORMAL
                self._release_count = 0
                self._refill_days = 1          # 进入周度回补期
        elif self.level == AlertLevel.L1_WATCH and dd < DD_L1:
            self.level = AlertLevel.NORMAL

        if self._refill_days > 0:
            self._refill_days += 1
        return self.level

    @property
    def pause_new_buys(self) -> bool:
        """一级及以上警戒：暂停新建仓位（表5.3）."""
        return self.level >= AlertLevel.L1_WATCH

    @property
    def equity_scale(self) -> float:
        """当前权益仓位系数（含解除后的周度逐级回补）."""
        if self._refill_days > 0 and self.level <= AlertLevel.L1_WATCH:
            start = EQUITY_SCALE[self._refill_from]
            weeks = (self._refill_days - 1) // REFILL_STEP_DAYS
            scale = start + (1.0 - start) * min(weeks / 2.0, 1.0)   # 两周回补到位
            if scale >= 1.0:
                self._refill_days = 0
                return 1.0
            return scale
        return EQUITY_SCALE[self.level]
