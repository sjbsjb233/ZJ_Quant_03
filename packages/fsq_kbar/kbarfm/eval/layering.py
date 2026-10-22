"""因子分层组合检验（报告7.3.2，图7.4）.

每个调仓截面按因子分值将股票池等分为10组（G1最低、G10最高），
构建等权组合并统计相对标的池等权基准的年化超额收益，检验因子
在全分值区间的排序质量与单调性。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 250


def layered_returns(
    factor: pd.DataFrame,
    prices: pd.DataFrame,
    n_groups: int = 10,
    rebalance_every: int = 5,
) -> pd.DataFrame:
    """返回各组的年化收益与年化超额（相对全池等权），并给出多空价差.

    factor / prices: date x symbol 面板。
    """
    dates = [d for d in factor.index if d in prices.index]
    rebal_dates = dates[::rebalance_every]

    daily_ret = prices.pct_change()
    group_nav = {g: [1.0] for g in range(1, n_groups + 1)}
    bench_nav = [1.0]
    current_groups: dict[int, list] = {}

    all_days: list[pd.Timestamp] = []
    for i, d0 in enumerate(rebal_dates):
        # 截面分组
        cross = factor.loc[d0].dropna()
        if len(cross) >= n_groups * 3:
            labels = pd.qcut(cross.rank(method="first"), n_groups, labels=False) + 1
            current_groups = {
                g: list(labels[labels == g].index) for g in range(1, n_groups + 1)
            }
        # 持有到下一个调仓日
        d_end = rebal_dates[i + 1] if i + 1 < len(rebal_dates) else dates[-1]
        hold_days = [d for d in dates if d0 < d <= d_end]
        for day in hold_days:
            if day not in daily_ret.index:
                continue
            rets = daily_ret.loc[day]
            bench = float(rets.dropna().mean()) if not rets.dropna().empty else 0.0
            bench_nav.append(bench_nav[-1] * (1.0 + bench))
            for g in range(1, n_groups + 1):
                members = current_groups.get(g, [])
                r = float(rets.reindex(members).dropna().mean()) if members else 0.0
                group_nav[g].append(group_nav[g][-1] * (1.0 + r))
            all_days.append(day)

    n_days = max(len(all_days), 1)
    ann = lambda nav: (nav[-1] / nav[0]) ** (TRADING_DAYS / n_days) - 1.0  # noqa: E731
    bench_ann = ann(bench_nav)
    rows = []
    for g in range(1, n_groups + 1):
        g_ann = ann(group_nav[g])
        rows.append(
            {"group": f"G{g}", "ann_return": g_ann, "ann_excess": g_ann - bench_ann}
        )
    df = pd.DataFrame(rows).set_index("group")
    df.attrs["long_short_spread"] = float(
        df.loc[f"G{n_groups}", "ann_excess"] - df.loc["G1", "ann_excess"]
    )
    df.attrs["monotonic"] = bool(np.all(np.diff(df["ann_excess"].to_numpy()) >= -1e-4))
    return df
