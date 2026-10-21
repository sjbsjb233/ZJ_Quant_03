"""因子有效性监控（5.2 / 6.2.2）.

以20个交易日滚动窗口逐日计算因子RankIC均值：
  失效判定：低于0.02且连续5个交易日 -> 暂停新建仓位、进入防御模式；
  解除判定：回升至0.03以上且连续5个交易日。
0.02参考"有效因子IC绝对值应大于0.02"的行业共识。
"""

from __future__ import annotations

import pandas as pd


def rolling_rank_ic(
    score: pd.Series, fwd_ret: pd.Series, window: int = 20
) -> pd.Series:
    """逐日横截面RankIC及其20日滚动均值."""
    df = pd.concat([score.rename("s"), fwd_ret.rename("r")], axis=1).dropna()
    daily_ic = df.groupby(level="datetime").apply(
        lambda g: g["s"].corr(g["r"], method="spearman")
    )
    return daily_ic.rolling(window).mean().rename("ric20")


class FactorGuard:
    def __init__(
        self, fail: float = 0.02, recover: float = 0.03, days: int = 5
    ) -> None:
        self.fail = fail
        self.recover = recover
        self.days = days

    def run(self, ric20: pd.Series) -> pd.Series:
        """-> 防御模式bool序列（True=暂停新建仓位，仅存量管理）."""
        defense = False
        below, above = 0, 0
        out = []
        for v in ric20.fillna(self.recover):
            if not defense:
                below = below + 1 if v < self.fail else 0
                if below >= self.days:
                    defense = True
                    above = 0
            else:
                above = above + 1 if v > self.recover else 0
                if above >= self.days:
                    defense = False
                    below = 0
            out.append(defense)
        return pd.Series(out, index=ric20.index, name="defense")
