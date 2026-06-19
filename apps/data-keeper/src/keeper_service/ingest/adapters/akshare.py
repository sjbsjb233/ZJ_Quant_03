"""akshare adapter — the primary source (主源, V3 §11 Q1 / 附录 A).

Each ``fetch_*`` returns a list of dicts using **quant_data's standard column
names** so the ingest helpers can run with ``apply_mapping=False`` (the rename
is done here, not via metadata). akshare is imported lazily — the deployed image
installs it via the ``[ingest]`` extra; dev does not.

Note: akshare endpoints + column labels drift across versions; the mappings
below target the common 1.1x layout and are the one place to adjust if a column
label changes upstream.
"""
from __future__ import annotations

import datetime as dt

_AK = None


def _ak():
    global _AK
    if _AK is None:
        try:
            import akshare as ak  # type: ignore
        except ImportError as e:  # pragma: no cover - only in deployed image
            raise RuntimeError(
                "akshare 未安装。请用部署镜像 (pip install 'data-keeper[ingest]') 或关闭采集调度。"
            ) from e
        _AK = ak
    return _AK


def _ymd(d) -> str:
    if isinstance(d, (dt.date, dt.datetime)):
        return d.strftime("%Y%m%d")
    return str(d).replace("-", "")[:8]


def _bare(symbol: str) -> str:
    """600519.SH -> 600519 (akshare 用纯代码)。"""
    return symbol.split(".")[0]


def _suffix(symbol: str) -> str:
    return symbol.split(".")[-1].upper() if "." in symbol else ""


# 日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 振幅, 涨跌幅, 涨跌额, 换手率
_BARS_COLS = {
    "日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low",
    "成交量": "volume", "成交额": "amount", "换手率": "turnover",
}


def fetch_bars_daily(symbols, start=None, end=None) -> list[dict]:
    ak = _ak()
    start_s = _ymd(start) if start else "19900101"
    end_s = _ymd(end) if end else dt.date.today().strftime("%Y%m%d")
    out: list[dict] = []
    for sym in symbols or []:
        df = ak.stock_zh_a_hist(symbol=_bare(sym), period="daily",
                                start_date=start_s, end_date=end_s, adjust="")
        if df is None or df.empty:
            continue
        for rec in df.rename(columns=_BARS_COLS).to_dict("records"):
            date = rec.get("date")
            close = rec.get("close")
            out.append({
                "date": dt.date.fromisoformat(str(date)[:10]) if date is not None else None,
                "symbol": sym,
                "open": rec.get("open"),
                "high": rec.get("high"),
                "low": rec.get("low"),
                "close": close,
                "pre_close": None,
                # akshare 成交量 单位=手 (100 股); 成交额 单位=元。统一换算成「股」,
                # 与 tushare 备源 (vol 手 *100) 一致,否则同一字段单位随主/备源漂移。
                "volume": (rec.get("volume") or 0) * 100,
                "amount": rec.get("amount"),
                "vwap": close,
                "adj_factor": 1.0,
                "turnover": rec.get("turnover"),
            })
    return out


def fetch_calendar(market="CN-A", start=None, end=None) -> list[dict]:
    ak = _ak()
    df = ak.tool_trade_date_hist_sina()  # column: trade_date
    if df is None or df.empty:
        return []
    days = [dt.date.fromisoformat(str(d)[:10]) for d in df["trade_date"].tolist()]
    lo = dt.date.fromisoformat(str(_ymd(start))[:4] + "-" + str(_ymd(start))[4:6] + "-" + str(_ymd(start))[6:8]) if start else None
    out = []
    for i, d in enumerate(days):
        if lo and d < lo:
            continue
        nxt = days[i + 1] if i + 1 < len(days) else None
        out.append({"date": d, "market": market, "is_trading_day": True,
                    "next_trading_day": nxt, "trading_session": "full",
                    "holiday_name": None})
    return out
