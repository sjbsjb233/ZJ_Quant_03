"""K-line endpoints (doc §4.6.2 / §6.7) -- candles, marks, multi, source-diff."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends

from quant_data import charts as qd_charts

from ...deps import get_current_user, get_reader, get_root
from ...errors import ApiError
from ..schemas import KlineMultiRequest
from ._util import candle, table_for_frequency, to_ms

router = APIRouter(prefix="/kline", tags=["charts:kline"])

_MAX_CANDLES = 5000


def _read_candles(reader, symbol, frequency, adjust, start, end, source, view):
    table = table_for_frequency(frequency)
    if view == "all" or (source and source != "current_view"):
        # specific source / full view: read raw success rows, no re-adjust
        filters = [f"t.symbol = '{symbol.replace(chr(39), chr(39) * 2)}'"]
        tbl = reader.read_full_view(table, filters=filters,
                                    order_by=("date" if table == "raw.bars_daily" else "datetime"))
        rows = [r for r in tbl.to_pylist() if r.get("_status") == "success"]
        if source and source != "current_view":
            rows = [r for r in rows if r.get("_source") == source]
        rows = _date_window(rows, table, start, end)
    elif table == "raw.bars_daily":
        tbl = reader.read_bars_daily(symbols=[symbol], start=start, end=end, adjust=adjust)
        rows = tbl.to_pylist()
    else:
        tbl = reader.read_bars_minute(symbols=[symbol], start=start, end=end,
                                      frequency=frequency, adjust=adjust)
        rows = tbl.to_pylist()
    return rows


def _date_window(rows, table, start, end):
    col = "date" if table == "raw.bars_daily" else "datetime"

    def d(v):
        return v.date() if isinstance(v, dt.datetime) else v

    if start:
        s = dt.date.fromisoformat(str(start)[:10])
        rows = [r for r in rows if r.get(col) is not None and d(r[col]) >= s]
    if end:
        e = dt.date.fromisoformat(str(end)[:10])
        rows = [r for r in rows if r.get(col) is not None and d(r[col]) <= e]
    return rows


@router.get("")
def kline(
    symbol: str, reader=Depends(get_reader), user=Depends(get_current_user),
    frequency: str = "1d", adjust: str = "pre", source: str = "current_view",
    start: str | None = None, end: str | None = None, count: int | None = None,
    before: str | None = None,
    view: str = "current", include_extras: bool = False,
):
    if frequency in ("1m", "5m", "15m", "30m", "1h") and start and end:
        if (dt.date.fromisoformat(end[:10]) - dt.date.fromisoformat(start[:10])).days > 30:
            raise ApiError("INVALID_DATE_RANGE", "分钟级 K 线时间窗不能超过 30 天")
    try:
        rows = _read_candles(reader, symbol, frequency, adjust, start, end, source, view)
    except ValueError as e:
        raise ApiError("INVALID_PARAMETER", str(e)) from e

    rows.sort(key=lambda r: r.get("date") or r.get("datetime") or dt.datetime.min)
    has_more_before = False
    # loadMore cursor: keep only rows strictly earlier than `before`, then the
    # `count` window below yields the N most-recent of those (doc v1.0 §3.8/B.1).
    # For daily candles (or a date-only cursor) we page at day granularity; for
    # intraday candles a full ISO datetime cursor pages at timestamp granularity
    # so we don't drop the partial cutoff day.
    if before:
        is_daily = table_for_frequency(frequency) == "raw.bars_daily"
        col = "date" if is_daily else "datetime"
        if is_daily or len(before) <= 10:
            b = dt.date.fromisoformat(before[:10])

            def _key(v):
                return v.date() if isinstance(v, dt.datetime) else v
        else:
            # stored datetimes are naive (treated as UTC); drop any tz suffix
            s = before.replace("Z", "").replace("+00:00", "")
            b = dt.datetime.fromisoformat(s[:26])

            def _key(v):
                return v if isinstance(v, dt.datetime) else dt.datetime(v.year, v.month, v.day)

        rows = [r for r in rows if r.get(col) is not None and _key(r[col]) < b]
    if count and len(rows) > count:
        rows = rows[-count:]
        has_more_before = True
    truncated = False
    if len(rows) > _MAX_CANDLES:
        rows = rows[-_MAX_CANDLES:]
        has_more_before = truncated = True
    candles = [candle(r, include_extras) for r in rows]
    return {
        "symbol": symbol, "frequency": frequency, "adjust": adjust,
        "candles": candles, "has_more_before": has_more_before,
        "has_more_after": False, "truncated": truncated,
    }


@router.get("/marks")
def marks(
    symbol: str, root: str = Depends(get_root), user=Depends(get_current_user),
    start: str | None = None, end: str | None = None,
    frequency: str = "1d", include: str = "corporate_actions,batch_revisions",
):
    want = {x.strip() for x in include.split(",")}
    out = {"corporate_actions": [], "batch_revisions": []}
    if "corporate_actions" in want:
        for a in qd_charts.get_corporate_actions(symbol, start=start, end=end, root=root):
            out["corporate_actions"].append({
                "date": a["date"].isoformat() if hasattr(a["date"], "isoformat") else a["date"],
                "action_type": a["action_type"], "amount": a["amount"],
                "ratio": a["ratio"], "description": a["description"],
            })
    if "batch_revisions" in want:
        for r in qd_charts.get_bar_revisions(symbol, frequency, start=start, end=end, root=root):
            out["batch_revisions"].append({
                "start": r["start"].isoformat(), "end": r["end"].isoformat(),
                "batch_id": r["batch_id"], "status": r["status"],
                "superseded_by": r["superseded_by"], "reason": r["reason"],
            })
    return out


@router.post("/multi")
def multi(body: KlineMultiRequest, reader=Depends(get_reader), user=Depends(get_current_user)):
    if len(body.symbols) > 5:
        raise ApiError("INVALID_PARAMETER", "对比模式最多 5 个 symbol")
    result = {}
    for sym in body.symbols:
        rows = _read_candles(reader, sym, body.frequency, body.adjust,
                             body.start, body.end, "current_view", "current")
        rows.sort(key=lambda r: r.get("date") or r.get("datetime") or dt.datetime.min)
        if body.mode == "overlay":
            result[sym] = [{"timestamp": to_ms(r.get("date") or r.get("datetime")),
                            "close": r.get("close")} for r in rows]
        else:
            result[sym] = [candle(r) for r in rows]
    return {"mode": body.mode, "frequency": body.frequency, "series": result}


@router.get("/diff-sources")
def diff_sources(
    symbol: str, source_a: str, source_b: str,
    reader=Depends(get_reader), user=Depends(get_current_user),
    frequency: str = "1d", start: str | None = None, end: str | None = None,
):
    table = table_for_frequency(frequency)
    filters = [f"t.symbol = '{symbol.replace(chr(39), chr(39) * 2)}'"]
    tbl = reader.read_full_view(table, filters=filters,
                                order_by=("date" if table == "raw.bars_daily" else "datetime"))
    rows = [r for r in tbl.to_pylist() if r.get("_status") == "success"]
    rows = _date_window(rows, table, start, end)

    def series(src):
        rs = sorted((r for r in rows if r.get("_source") == src),
                    key=lambda r: r.get("date") or r.get("datetime") or dt.datetime.min)
        return [candle(r) for r in rs]

    return {"symbol": symbol, "frequency": frequency,
            "source_a": source_a, "source_b": source_b,
            "candles_a": series(source_a), "candles_b": series(source_b)}
