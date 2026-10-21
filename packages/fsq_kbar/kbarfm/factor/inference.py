"""全标的池分位数推理引擎（报告4.2.3 / 4.2.4）.

对每只股票在每个交易日：以过去90个交易日的日频K线为回看窗口
输入模型，由分位数预测头单次确定性前向输出未来H=10个交易日
累计收益的 q0.1、q0.5、q0.9。全池标的按批拼接，一次前向完成，
无随机采样、无多路径蒙特卡洛。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import torch

from ..data.fields import ALL_FIELDS
from ..data.normalize import time_features, zscore_clip
from ..model.foundation import KBarFoundationModel
from ..model.tokenizer import KBarTokenizer
from ..utils.logging import get_logger

log = get_logger("kbarfm.factor.inference")


class FactorEngine:
    def __init__(
        self,
        tokenizer: KBarTokenizer,
        model: KBarFoundationModel,
        lookback: int = 90,
        device: str | None = None,
        batch_size: int = 256,
    ) -> None:
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = torch.device(device)
        self.tokenizer = tokenizer.to(self.device).eval()
        self.model = model.to(self.device).eval()
        self.lookback = lookback
        self.batch_size = batch_size
        self.quantiles = model.quantiles

    @torch.no_grad()
    def quantiles_for_date(
        self,
        panel: dict[str, pd.DataFrame],
        date: pd.Timestamp,
    ) -> pd.DataFrame:
        """对单个交易日全池推理.

        panel: symbol -> OHLCVA日频DataFrame（DatetimeIndex，后复权口径）。
        返回 DataFrame(index=symbol, columns=[q10, q50, q90])。
        """
        date = pd.Timestamp(date)
        windows, stamps, symbols = [], [], []
        for symbol, df in panel.items():
            hist = df.loc[:date]
            if len(hist) < self.lookback:
                continue
            win = hist.iloc[-self.lookback :]
            x = win[list(ALL_FIELDS)].to_numpy(dtype=np.float32)
            if not np.isfinite(x[:, :4]).all():          # 窗口内价格必须完整
                continue
            x_norm, _, _ = zscore_clip(x)
            windows.append(x_norm)
            stamps.append(time_features(win.index))
            symbols.append(symbol)

        if not symbols:
            return pd.DataFrame(columns=["q10", "q50", "q90"])

        out = np.empty((len(symbols), len(self.quantiles)), dtype=np.float32)
        for lo in range(0, len(symbols), self.batch_size):
            hi = min(lo + self.batch_size, len(symbols))
            xb = torch.from_numpy(np.stack(windows[lo:hi])).to(self.device)
            sb = torch.from_numpy(np.stack(stamps[lo:hi])).to(self.device)
            codes = self.tokenizer.encode(xb)
            q = self.model.predict_quantiles(codes, sb)   # 确定性单次前向
            out[lo:hi] = q.float().cpu().numpy()

        cols = [f"q{int(round(q * 100))}" for q in self.quantiles]
        return pd.DataFrame(out, index=pd.Index(symbols, name="symbol"), columns=cols)

    @torch.no_grad()
    def quantile_panel(
        self,
        panel: dict[str, pd.DataFrame],
        dates: list[pd.Timestamp],
    ) -> pd.DataFrame:
        """滚动生成多日分位数面板：MultiIndex(date, symbol) x [q10, q50, q90]."""
        frames = []
        for i, date in enumerate(dates):
            df = self.quantiles_for_date(panel, date)
            if df.empty:
                continue
            df = df.assign(date=pd.Timestamp(date)).set_index("date", append=True)
            frames.append(df.reorder_levels(["date", "symbol"]))
            if (i + 1) % 20 == 0:
                log.info("分位数推理进度: %d / %d 个交易日", i + 1, len(dates))
        if not frames:
            return pd.DataFrame(columns=["q10", "q50", "q90"])
        return pd.concat(frames).sort_index()
