"""组合回撤三级递进式警戒机制（5.2，表5.3）.

一级警戒（5% <= DD < 8%）：暂停新建仓位，仅存量必要调仓；
二级警戒（8% <= DD < 12%）：降仓至目标权重50%-70%，上调对冲与现金；
三级警戒（DD >= 12%）：降仓至20%-30%，完全对冲，暂停全部开仓。
解除：DD自低点修复至8%以内且连续3个交易日满足，按周度逐级回补。

状态机以日频净值离线推演，输出各日警戒层级与目标风险度
（risk_degree），供第二遍回测与对冲比例阶梯使用——与实盘在线
状态机逐日等价。
"""

from __future__ import annotations

import pandas as pd


class DrawdownGuard:
    def __init__(
        self,
        levels: tuple[float, float, float] = (0.05, 0.08, 0.12),
        exposures: tuple[float, float, float, float] = (0.97, 0.97, 0.60, 0.25),
        release_dd: float = 0.08,
        release_days: int = 3,
        step_down_every: int = 5,   # 解除后按周度（5个交易日）逐级回补
    ) -> None:
        self.levels = tuple(levels)
        self.exposures = tuple(exposures)
        self.release_dd = release_dd
        self.release_days = release_days
        self.step_down_every = step_down_every

    def _dd_level(self, dd: float) -> int:
        if dd >= self.levels[2]:
            return 3
        if dd >= self.levels[1]:
            return 2
        if dd >= self.levels[0]:
            return 1
        return 0

    def run(self, nav: pd.Series) -> pd.DataFrame:
        """nav: 日频净值Series -> DataFrame[dd, level, exposure, freeze_new]."""
        peak = nav.cummax()
        dd = (1.0 - nav / peak).clip(lower=0.0)

        levels, exposures, freeze = [], [], []
        state = 0            # 当前警戒层级（含滞回）
        ok_days = 0          # 连续满足解除条件的天数
        since_release = 0    # 满足解除后经过的交易日数（周度回补节拍）

        for d in dd:
            raw = self._dd_level(d)
            if raw > state:
                # 警戒升级立即生效
                state = raw
                ok_days = 0
                since_release = 0
            elif state > 0:
                # 解除条件：回撤修复至release_dd以内（表5.3）
                if d < self.release_dd:
                    ok_days += 1
                else:
                    ok_days = 0
                    since_release = 0
                if ok_days >= self.release_days:
                    since_release += 1
                    if since_release >= self.step_down_every:
                        state -= 1               # 按周度逐级回补
                        since_release = 0

            levels.append(state)
            exposures.append(self.exposures[state])
            freeze.append(state >= 1)            # 一级及以上：暂停新建仓位

        return pd.DataFrame(
            {
                "dd": dd,
                "level": levels,
                "exposure": exposures,
                "freeze_new": freeze,
            },
            index=nav.index,
        )
