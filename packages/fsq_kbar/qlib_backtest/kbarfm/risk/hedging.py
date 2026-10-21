"""股指期货分级动态对冲（5.4，表5.4，图5.3）.

"常态零对冲＋分级动态对冲"：常态不持有对冲仓位，保留完整Beta与
选股Alpha；风险信号触发后逐级启用——
  一级警戒观察期            -> 20% 基础对冲
  单一二级条件（任一）      -> 60%
    ① 回撤触及二级警戒（DD>=8%）
    ② 标的池>30%成分股处横截面波动率前20%分位
    ③ 拥挤标的占持仓比例>20%
  两项及以上二级条件共振    -> 80%
  三级回撤警戒（DD>=12%）   -> 100%（净敞口归零）
解除：相应条件解除且连续3个交易日，之后按周度逐级回补退出。

对冲以IF（沪深300池）/ IC（中证800池）合约空头执行，张数 =
组合市值 × 对冲比例 ÷（合约乘数 × 期货价格）。回测中以基准指数
收益率作为期货腿的代理，对冲后收益按叠加口径核算。
"""

from __future__ import annotations

import math

import pandas as pd


def hedge_ratio_series(
    dd_level: pd.Series,
    vol_breadth: pd.Series | None = None,
    crowd_share: pd.Series | None = None,
    ladder: tuple[float, ...] = (0.0, 0.2, 0.6, 0.8, 1.0),
    vol_breadth_thresh: float = 0.30,
    crowd_share_thresh: float = 0.20,
    release_days: int = 3,
) -> pd.Series:
    """按表5.4阶梯生成逐日对冲比例序列."""
    idx = dd_level.index
    vb = (vol_breadth.reindex(idx).fillna(0.0) if vol_breadth is not None
          else pd.Series(0.0, index=idx))
    cs = (crowd_share.reindex(idx).fillna(0.0) if crowd_share is not None
          else pd.Series(0.0, index=idx))

    raw = []
    for t in idx:
        lvl = int(dd_level.loc[t])
        cond2 = int(lvl >= 2) + int(vb.loc[t] > vol_breadth_thresh) + int(
            cs.loc[t] > crowd_share_thresh
        )
        if lvl >= 3:
            r = ladder[4]          # 100% 完全对冲
        elif cond2 >= 2:
            r = ladder[3]          # 80% 多重条件共振
        elif cond2 == 1:
            r = ladder[2]          # 60% 单一二级条件
        elif lvl >= 1:
            r = ladder[1]          # 20% 一级观察
        else:
            r = ladder[0]          # 常态零对冲
        raw.append(r)
    raw = pd.Series(raw, index=idx)

    # 解除滞回：比例仅在低档信号连续release_days日后才下调（表5.4）
    out, cur, calm = [], 0.0, 0
    for r in raw:
        if r > cur:
            cur, calm = r, 0
        elif r < cur:
            calm += 1
            if calm >= release_days:
                cur, calm = r, 0
        else:
            calm = 0
        out.append(cur)
    return pd.Series(out, index=idx, name="hedge_ratio")


def futures_contracts(
    portfolio_value: float, ratio: float, futures_price: float, multiplier: int
) -> int:
    """对冲张数 = 组合市值×对冲比例 ÷（合约乘数×期货价格），向下取整."""
    if ratio <= 0 or futures_price <= 0:
        return 0
    return int(math.floor(portfolio_value * ratio / (multiplier * futures_price)))


def hedged_returns(
    strat_ret: pd.Series, bench_ret: pd.Series, hedge_ratio: pd.Series
) -> pd.Series:
    """对冲叠加收益：空头期货腿以基准指数收益代理，用前一日比例避免前视."""
    ratio = hedge_ratio.reindex(strat_ret.index).fillna(0.0).shift(1).fillna(0.0)
    return (strat_ret - ratio * bench_ret.reindex(strat_ret.index).fillna(0.0)).rename(
        "hedged_return"
    )
