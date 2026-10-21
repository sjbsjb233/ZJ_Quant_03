"""基于已实现波动率预测的前瞻风险度量（5.1）.

以分位区间宽度 (q0.9 - q0.1) 度量收益分布离散程度，经"训练期内
拟合、样本外冻结"的单调映射校准为未来已实现波动率的估计量。
单调映射以池相邻违反者算法（PAVA）实现的等保回归拟合分箱均值，
样本外经分段线性插值应用——纯NumPy自研实现，无额外依赖。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def realized_vol_panel(close: pd.DataFrame, horizon: int = 10) -> pd.DataFrame:
    """未来H日已实现波动率（10日口径，与图5.1一致）.

    close: 宽表（index=datetime, columns=instrument）。
    rv_t = std(r_{t+1}, ..., r_{t+H})，r为日对数收益。
    """
    logret = np.log(close).diff()
    # 前瞻窗口：将未来H日收益的滚动std对齐到当前时点
    rv = logret.rolling(horizon).std().shift(-horizon)
    return rv


def _pava(y: np.ndarray, w: np.ndarray) -> np.ndarray:
    """池相邻违反者算法：加权非降等保拟合."""
    y = y.astype(np.float64).copy()
    w = w.astype(np.float64).copy()
    n = len(y)
    # 每个块记录 (均值, 权重)
    means, weights, sizes = [], [], []
    for i in range(n):
        means.append(y[i]); weights.append(w[i]); sizes.append(1)
        while len(means) > 1 and means[-2] > means[-1]:
            m2, w2, s2 = means.pop(), weights.pop(), sizes.pop()
            m1, w1, s1 = means.pop(), weights.pop(), sizes.pop()
            wt = w1 + w2
            means.append((m1 * w1 + m2 * w2) / wt)
            weights.append(wt); sizes.append(s1 + s2)
    out = np.empty(n)
    pos = 0
    for m, s in zip(means, sizes):
        out[pos : pos + s] = m
        pos += s
    return out


def fit_width_calibration(
    width: pd.Series, rv: pd.Series, n_bins: int = 50
) -> tuple[np.ndarray, np.ndarray]:
    """训练期内拟合 宽度->已实现波动率 的单调映射，返回插值节点(x, y).

    width/rv 按共同index对齐；分位数分箱后对箱内均值做PAVA等保回归。
    """
    df = pd.concat([width.rename("w"), rv.rename("rv")], axis=1).dropna()
    if len(df) < n_bins * 4:
        raise ValueError("校准样本不足")
    df["bin"] = pd.qcut(df["w"], q=n_bins, labels=False, duplicates="drop")
    g = df.groupby("bin").agg(x=("w", "mean"), y=("rv", "mean"), n=("rv", "size"))
    y_iso = _pava(g["y"].to_numpy(), g["n"].to_numpy())
    return g["x"].to_numpy(), y_iso


def apply_width_calibration(
    width: pd.Series, knots: tuple[np.ndarray, np.ndarray]
) -> pd.Series:
    """样本外冻结应用：分段线性插值（端点外取边界值）."""
    x, y = knots
    return pd.Series(
        np.interp(width.to_numpy(dtype=np.float64), x, y),
        index=width.index,
        name="vol_pred",
    )


def vol_breadth_series(vol_pred: pd.Series, top_q: float = 0.80) -> pd.Series:
    """标的池内处于横截面波动率前20%分位的成分股比例（表5.4二级条件②）.

    对横截面分位而言该比例恒为(1-top_q)，故此处以"预测波动率超过
    训练期全局阈值"的口径统计广度，刻画全池波动水平的抬升。
    """
    threshold = vol_pred.quantile(top_q)
    flag = vol_pred > threshold
    return flag.groupby(level="datetime").mean().rename("vol_breadth")
