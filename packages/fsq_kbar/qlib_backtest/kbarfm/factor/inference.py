"""全标的池批量确定性推理（4.2.3 / 4.2.4 / 表4.6）.

对每只股票在每个交易日：取过去90个交易日的日频K线为回看窗口，
窗口内逐维z-score标准化（与训练一致）后经冻结标记化器离散化，
由分位数预测头单次确定性前向输出未来H=10日累计收益的
q0.1 / q0.5 / q0.9（无随机采样、无蒙特卡洛路径），并同步给出
预测区间宽度 (q0.9 - q0.1) 供风控转化为前瞻波动率（5.1）。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import torch
from tqdm import tqdm

from ..data.normalize import time_features, zscore_clip
from ..data.qlib_source import (
    get_calendar,
    get_pool_instruments,
    init_qlib,
    load_ohlcva,
)
from ..model.io import load_backbone, load_tokenizer
from ..utils.logging import get_logger

logger = get_logger("kbarfm.inference")


def _instrument_arrays(
    pool: str, start: str, end: str, lookback: int
) -> dict[str, tuple[np.ndarray, np.ndarray, pd.DatetimeIndex]]:
    """预载各标的的OHLCVA数组（含lookback缓冲），返回 {inst: (x, ts, index)}."""
    # 缓冲区间：lookback个交易日约折算2倍自然日
    buffer_start = (pd.Timestamp(start) - pd.Timedelta(days=lookback * 2 + 30)).strftime("%Y-%m-%d")
    instruments = get_pool_instruments(pool, start, end)
    panel = load_ohlcva(instruments, buffer_start, end, freq="day")

    out = {}
    for inst, df in panel.groupby(level="instrument"):
        df = df.droplevel("instrument").dropna(subset=["open", "high", "low", "close"])
        if len(df) < lookback:
            continue
        x = df.to_numpy(dtype=np.float64)
        ts = (df.index.view("int64") // 10**9).astype(np.int64)
        out[inst] = (x, ts, df.index)
    return out


@torch.no_grad()
def generate_quantile_panel(
    cfg, pool: str, start: str, end: str, out_path: str | Path
) -> pd.DataFrame:
    """生成分位数预测面板：index=(datetime, instrument)，
    columns=[q10, q50, q90, ..., width]（简单收益口径）。
    """
    device = cfg.get("device", "cuda")
    init_qlib(cfg.provider_uri, cfg.region)

    tokenizer = load_tokenizer(cfg.tokenizer_ckpt, device)
    model = load_backbone(cfg.model_ckpt, device)
    lookback, batch_size = int(cfg.lookback), int(cfg.batch_size)
    q_names = [f"q{int(round(q * 100)):02d}" for q in model.quantiles]

    data = _instrument_arrays(pool, start, end, lookback)
    trade_dates = get_calendar(start, end, freq="day")
    logger.info("推理：%s池 %d 只标的 × %d 个交易日", pool, len(data), len(trade_dates))

    records: list[dict] = []
    for date in tqdm(trade_dates, desc=f"infer-{pool}"):
        # 组装当日横截面批次：要求该标的在date有bar且历史足够90根
        batch_x, batch_t, batch_inst = [], [], []
        for inst, (x, ts, index) in data.items():
            pos = index.searchsorted(date)
            if pos >= len(index) or index[pos] != date or pos + 1 < lookback:
                continue
            win = x[pos + 1 - lookback : pos + 1]
            xn, _m, _s = zscore_clip(win)               # 与训练同口径
            batch_x.append(xn)
            batch_t.append(time_features(ts[pos + 1 - lookback : pos + 1]))
            batch_inst.append(inst)
        if not batch_inst:
            continue

        for i in range(0, len(batch_inst), batch_size):
            xb = torch.from_numpy(np.stack(batch_x[i : i + batch_size])).to(device)
            tb = torch.from_numpy(np.stack(batch_t[i : i + batch_size])).to(device)
            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=(device == "cuda")):
                codes, _ = tokenizer.encode(xb)
                _logits, q_pred, _h = model(codes, tb)
            # 取末位置的分位数预测（对数收益）-> 简单收益
            q_last = torch.expm1(q_pred[:, -1, :].float()).cpu().numpy()
            for j, inst in enumerate(batch_inst[i : i + batch_size]):
                rec = {"datetime": date, "instrument": inst}
                rec.update({name: float(q_last[j, k]) for k, name in enumerate(q_names)})
                records.append(rec)

    panel = pd.DataFrame(records).set_index(["datetime", "instrument"]).sort_index()
    lo, hi = q_names[0], q_names[-1]
    panel["width"] = panel[hi] - panel[lo]               # 分位区间宽度（表4.6：风险度量）
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(out_path)
    logger.info("分位数面板 %s -> %s", panel.shape, out_path)
    return panel
