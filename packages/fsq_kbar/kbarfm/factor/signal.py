"""量价预测因子构建（报告4.2.4，附录B）.

三步流程：
  第一步 生成预测分位数（见 inference.py）；
  第二步 风险调整信号 α = q0.5 / (q0.9 - q0.1 + ε)，ε=1e-3——
        同等预期收益下预测区间越宽（模型把握越低）信号被相应
        压缩，"收益定方向、不确定性定置信"；
  第三步 横截面处理：1%/99%分位温和缩尾（表7.1）后做z-score
        标准化，统一到可比尺度供排序选股。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

EPS_DENOM = 1e-3


def risk_adjusted_score(
    q10: np.ndarray | pd.Series,
    q50: np.ndarray | pd.Series,
    q90: np.ndarray | pd.Series,
    eps: float = EPS_DENOM,
) -> np.ndarray | pd.Series:
    """α = q0.5 / (q0.9 - q0.1 + ε)；区间宽度经clip保证非负."""
    width = np.clip(np.asarray(q90) - np.asarray(q10), 0.0, None)
    return np.asarray(q50) / (width + eps)


def winsorize(s: pd.Series, lower: float = 0.01, upper: float = 0.99) -> pd.Series:
    """1%/99%分位温和缩尾（表7.1异常值处理）."""
    if s.dropna().empty:
        return s
    lo, hi = s.quantile(lower), s.quantile(upper)
    return s.clip(lo, hi)


def cross_sectional_zscore(s: pd.Series) -> pd.Series:
    """单个截面的z-score标准化."""
    mu, sd = s.mean(), s.std(ddof=0)
    if not np.isfinite(sd) or sd <= 0:
        return s * 0.0
    return (s - mu) / sd


def build_factor_frame(
    quantile_panel: pd.DataFrame,
    eps: float = EPS_DENOM,
    winsor: tuple[float, float] = (0.01, 0.99),
) -> pd.DataFrame:
    """分位数面板 -> 因子面板.

    quantile_panel: MultiIndex(date, symbol) x [q10, q50, q90]。
    返回同索引的 DataFrame[alpha_raw, factor, width]：
      alpha_raw  风险调整原始信号
      factor     缩尾+横截面z-score后的量价预测因子（选股依据）
      width      预测区间宽度 q0.9-q0.1（前瞻风险度量的输入，5.1节）
    """
    df = quantile_panel.copy()
    df["width"] = (df["q90"] - df["q10"]).clip(lower=0.0)
    df["alpha_raw"] = risk_adjusted_score(df["q10"], df["q50"], df["q90"], eps=eps)

    def _per_date(group: pd.DataFrame) -> pd.DataFrame:
        s = winsorize(group["alpha_raw"], *winsor)
        group["factor"] = cross_sectional_zscore(s)
        return group

    df = df.groupby(level="date", group_keys=False).apply(_per_date)
    return df[["alpha_raw", "factor", "width"]]
