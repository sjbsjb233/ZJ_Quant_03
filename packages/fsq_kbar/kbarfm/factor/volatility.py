"""前瞻波动率度量（报告5.1，表5.1）.

以分位区间宽度 (q0.9 - q0.1) 度量收益分布的离散程度，并经
"训练期内拟合、样本外冻结"的单调映射校准为未来已实现波动率
的估计量。单调映射以保序回归（PAVA，pool adjacent violators）
实现，无需外部依赖；波动率估计与因子构建共用同一次确定性
前向，无额外采样开销。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def realized_volatility(close: pd.Series, horizon: int = 10) -> pd.Series:
    """未来H日已实现波动率：日对数收益在 (t, t+H] 的标准差.

    realized_vol[t] = std( log-ret_{t+1 .. t+H} )，尾部不足H日为NaN。
    """
    log_ret = np.log(close / close.shift(1))
    fwd = pd.Series(index=close.index, dtype=float)
    values = log_ret.to_numpy()
    n = len(values)
    for i in range(n):
        window = values[i + 1 : i + 1 + horizon]
        if len(window) == horizon and np.isfinite(window).all():
            fwd.iloc[i] = float(np.std(window, ddof=1))
    return fwd


def _pava_isotonic(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """保序回归：返回按x升序的（x_sorted, y_fitted单调不减）."""
    order = np.argsort(x)
    xs, ys = x[order], y[order].astype(np.float64)
    weights = np.ones_like(ys)
    # 逐块合并违反单调性的相邻区段
    values = list(ys)
    wts = list(weights)
    idx_end = list(range(1, len(ys) + 1))
    i = 0
    while i < len(values) - 1:
        if values[i] > values[i + 1] + 1e-15:
            merged_w = wts[i] + wts[i + 1]
            merged_v = (values[i] * wts[i] + values[i + 1] * wts[i + 1]) / merged_w
            values[i : i + 2] = [merged_v]
            wts[i : i + 2] = [merged_w]
            idx_end[i : i + 2] = [idx_end[i + 1]]
            i = max(i - 1, 0)
        else:
            i += 1
    fitted = np.empty_like(ys)
    start = 0
    for v, end in zip(values, idx_end):
        fitted[start:end] = v
        start = end
    return xs, fitted


class VolatilityCalibrator:
    """区间宽度 -> 未来已实现波动率的单调映射（训练期拟合、样本外冻结）."""

    def __init__(self) -> None:
        self._grid_x: np.ndarray | None = None
        self._grid_y: np.ndarray | None = None

    @property
    def fitted(self) -> bool:
        return self._grid_x is not None

    def fit(self, widths: np.ndarray | pd.Series, realized: np.ndarray | pd.Series) -> "VolatilityCalibrator":
        w = np.asarray(widths, dtype=np.float64)
        r = np.asarray(realized, dtype=np.float64)
        valid = np.isfinite(w) & np.isfinite(r)
        if valid.sum() < 100:
            raise ValueError(f"校准样本不足: {int(valid.sum())} 条（至少100条）")
        self._grid_x, self._grid_y = _pava_isotonic(w[valid], r[valid])
        return self

    def predict(self, widths: np.ndarray | pd.Series) -> np.ndarray:
        if not self.fitted:
            raise RuntimeError("请先在训练期数据上调用 fit()（样本外区间冻结映射）")
        w = np.asarray(widths, dtype=np.float64)
        # 分段常数单调映射的线性插值；区间外取边界值（冻结外推）
        return np.interp(w, self._grid_x, self._grid_y)

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame({"width": self._grid_x, "vol": self._grid_y})

    @classmethod
    def from_frame(cls, df: pd.DataFrame) -> "VolatilityCalibrator":
        obj = cls()
        obj._grid_x = df["width"].to_numpy(dtype=np.float64)
        obj._grid_y = df["vol"].to_numpy(dtype=np.float64)
        return obj

    def evaluate(
        self, widths: np.ndarray | pd.Series, realized: np.ndarray | pd.Series
    ) -> dict:
        """MAE与R²（表5.1口径）."""
        pred = self.predict(widths)
        r = np.asarray(realized, dtype=np.float64)
        valid = np.isfinite(pred) & np.isfinite(r)
        pred, r = pred[valid], r[valid]
        mae = float(np.mean(np.abs(pred - r)))
        ss_res = float(np.sum((r - pred) ** 2))
        ss_tot = float(np.sum((r - r.mean()) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
        return {"mae": mae, "r2": r2, "n": int(valid.sum())}
