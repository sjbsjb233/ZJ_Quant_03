"""从预测信号到量价预测因子（4.2.4）.

三步：
  ① 分位数预测（inference.py输出的面板）；
  ② 风险调整信号 alpha = q0.5 / (q0.9 - q0.1 + eps)——中位数定方向、
     区间宽度定置信，同等预期收益下预测区间越宽信号被相应压缩；
  ③ 横截面标准化：每个交易日对全部标的z-score，统一到可比尺度。

另含风控前置处理（5.1 / 6.1.1）：预测波动率前20%分位降权、
极端者剔除，拥挤标的降权/剔除，直接作用于打分。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def risk_adjusted_raw(
    panel: pd.DataFrame,
    quantile_pair: tuple[float, float] = (0.1, 0.9),
    epsilon: float = 1e-3,
    mid: float = 0.5,
) -> pd.Series:
    """alpha = q_mid / (q_hi - q_lo + eps)（附录B）."""
    lo = f"q{int(round(quantile_pair[0] * 100)):02d}"
    hi = f"q{int(round(quantile_pair[1] * 100)):02d}"
    md = f"q{int(round(mid * 100)):02d}"
    width = (panel[hi] - panel[lo]).clip(lower=0.0)
    return panel[md] / (width + epsilon)


def cross_sectional_zscore(raw: pd.Series) -> pd.Series:
    """逐交易日横截面z-score（4.2.4第三步）."""
    def _z(s: pd.Series) -> pd.Series:
        std = s.std()
        return (s - s.mean()) / (std if std > 1e-12 else 1.0)

    return raw.groupby(level="datetime", group_keys=False).apply(_z)


def build_score(
    panel: pd.DataFrame,
    quantile_pair: tuple[float, float] = (0.1, 0.9),
    epsilon: float = 1e-3,
) -> pd.DataFrame:
    """输出Qlib可用的打分面板：index=(datetime, instrument)，列 'score'."""
    raw = risk_adjusted_raw(panel, quantile_pair, epsilon)
    score = cross_sectional_zscore(raw)
    return score.rename("score").to_frame()


def apply_risk_overlays(
    score: pd.DataFrame,
    vol_pred: pd.Series | None = None,
    crowded: pd.Series | None = None,
    vol_downweight_q: float = 0.80,
    vol_exclude_q: float = 0.95,
    downweight_scale: float = 0.7,
) -> pd.DataFrame:
    """风险预算与拥挤处置前置到打分（5.1 / 5.2 / 6.1.1）.

    - 预测波动率横截面前20%分位：降权（正分按比例压缩）；
    - 极端突出（前5%）：直接剔除（打分置NaN，不参与top-k）；
    - 拥挤判定（表5.2注：任两项信号共振）：同剔除处理。
    """
    out = score.copy()
    s = out["score"]

    if vol_pred is not None:
        vol_pred = vol_pred.reindex(s.index)
        rank = vol_pred.groupby(level="datetime", group_keys=False).rank(pct=True)
        downweight = (rank >= vol_downweight_q) & (rank < vol_exclude_q)
        exclude = rank >= vol_exclude_q
        s = s.where(~(downweight & (s > 0)), s * downweight_scale)
        s = s.mask(exclude, np.nan)

    if crowded is not None:
        crowded = crowded.reindex(s.index).fillna(False).astype(bool)
        s = s.mask(crowded, np.nan)

    out["score"] = s
    return out
