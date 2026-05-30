"""tushare adapter — the fallback source (备源, V3 §11 Q1).

Used when akshare fails / rate-limits. Requires a Pro token via ``TUSHARE_TOKEN``.
Imported lazily (deployed image only). Returns quant_data's standard column names
so the ingest helpers run with ``apply_mapping=False``.
"""
from __future__ import annotations

import datetime as dt
import os

_PRO = None


def _pro():
    global _PRO
    if _PRO is None:
        try:
            import tushare as ts  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "tushare 未安装。请用部署镜像 (pip install 'data-keeper[ingest]') 或关闭采集调度。"
            ) from e
        token = os.environ.get("TUSHARE_TOKEN")
        if not token:
            raise RuntimeError("TUSHARE_TOKEN 未配置,无法使用 tushare 备源。")
        ts.set_token(token)
        _PRO = ts.pro_api()
    return _PRO


def _ymd(d) -> str:
    if isinstance(d, (dt.date, dt.datetime)):
        return d.strftime("%Y%m%d")
    return str(d).replace("-", "")[:8]


def fetch_bars_daily(symbols, start=None, end=None) -> list[dict]:
    pro = _pro()
    start_s = _ymd(start) if start else "19900101"
    end_s = _ymd(end) if end else dt.date.today().strftime("%Y%m%d")
    out: list[dict] = []
    for sym in symbols or []:
        # tushare ts_code already uses the SH/SZ suffix form (600519.SH).
        df = pro.daily(ts_code=sym, start_date=start_s, end_date=end_s)
        if df is None or df.empty:
            continue
        for rec in df.to_dict("records"):
            td = str(rec.get("trade_date"))
            close = rec.get("close")
            out.append({
                "date": dt.date(int(td[:4]), int(td[4:6]), int(td[6:8])) if len(td) == 8 else None,
                "symbol": sym,
                "open": rec.get("open"),
                "high": rec.get("high"),
                "low": rec.get("low"),
                "close": close,
                "pre_close": rec.get("pre_close"),
                # tushare vol 单位=手 (100 股); amount 单位=千元.
                "volume": (rec.get("vol") or 0) * 100,
                "amount": (rec.get("amount") or 0) * 1000,
                "vwap": close,
                "adj_factor": 1.0,
            })
    return out
