"""因子信息衰减特性（报告7.3.3，图7.5）.

RankIC随前瞻期H的变化曲线：检验因子捕捉的是未来两周量级的
中短期趋势（峰值应在H=10附近），信号持续期需覆盖调仓节奏与
最短持有期设定。
"""

from __future__ import annotations

import pandas as pd

from .ic import ic_series


def ic_decay(
    factor: pd.DataFrame,
    prices: pd.DataFrame,
    horizons: tuple[int, ...] = (1, 2, 3, 5, 8, 10, 12, 15, 20, 30),
) -> pd.DataFrame:
    """各前瞻期的RankIC均值与ICIR."""
    rows = []
    for h in horizons:
        ric = ic_series(factor, prices, horizon=h, method="spearman")
        rows.append(
            {
                "horizon": h,
                "rank_ic_mean": float(ric.mean()),
                "icir": float(ric.mean() / ric.std()) if ric.std() > 0 else float("nan"),
                "n_days": int(len(ric)),
            }
        )
    return pd.DataFrame(rows).set_index("horizon")
