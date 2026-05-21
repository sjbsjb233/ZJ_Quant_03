"""Chart support data for the K-line workstation (doc §7.9).

Two overlays the price chart needs that aren't plain OHLCV:

* ``get_corporate_actions`` -- ex-date markers (dividends / splits / rights).
* ``get_bar_revisions`` -- date ranges that an *invalidated* batch once wrote,
  so the chart can shade "this region was revised" (doc §4.6.2).
"""
from __future__ import annotations

import datetime as dt

from .metadata import batches
from .reader import DataReader, _date_lit, _str_lit

_DESC = {
    "cash_dividend": "现金分红",
    "stock_dividend": "送股",
    "transfer": "转增",
    "rights_issue": "配股",
    "split": "拆股",
    "name_change": "更名",
    "suspend": "停牌",
    "resume": "复牌",
    "delisting": "退市",
}


def get_corporate_actions(symbol: str, start=None, end=None, root=None) -> list[dict]:
    """Corporate-action ex-date marks for one symbol in a window."""
    reader = DataReader(root=root)
    filters = [f"t.symbol = {_str_lit(symbol)}"]
    if start is not None:
        filters.append(f"t.action_date >= {_date_lit(start)}")
    if end is not None:
        filters.append(f"t.action_date <= {_date_lit(end)}")
    rows = reader.current_view(
        "raw.corporate_actions", filters=filters, order_by="action_date"
    ).to_pylist()
    out = []
    for r in rows:
        at = r["action_type"]
        amount = r.get("cash_per_share")
        ratio = None
        if r.get("split_ratio"):
            ratio = f"1:{r['split_ratio']:g}"
        elif r.get("stock_per_10"):
            ratio = f"10送{r['stock_per_10']:g}"
        elif r.get("transfer_per_10"):
            ratio = f"10转{r['transfer_per_10']:g}"
        out.append({
            "date": r["action_date"],
            "action_type": at,
            "amount": amount,
            "ratio": ratio,
            "description": _DESC.get(at, at),
        })
    return out


def get_bar_revisions(symbol: str, frequency: str = "1d", start=None, end=None,
                      root=None) -> list[dict]:
    """Date ranges this symbol's bars were written by a now-invalidated batch.

    Reads each invalidated batch's raw rows (bypassing the current view), keeps
    those for ``symbol`` inside the window, and reports the min/max bar date as a
    revision band.
    """
    table = "raw.bars_daily" if frequency in ("1d", "1w", "1M", "1D", "1W") else "raw.bars_minute"
    time_col = "date" if table == "raw.bars_daily" else "datetime"
    reader = DataReader(root=root)
    s = _to_date(start) if start is not None else None
    e = _to_date(end) if end is not None else None

    out = []
    for b in batches.list_batches(target_table=table, root=root):
        if b["status"] != "invalidated":
            continue
        try:
            tbl = reader.read_batch(b["batch_id"], table)
        except Exception:  # noqa: BLE001
            continue
        rows = [r for r in tbl.to_pylist() if r.get("symbol") == symbol]
        dates = [_to_date(r[time_col]) for r in rows if r.get(time_col) is not None]
        if s is not None:
            dates = [d for d in dates if d >= s]
        if e is not None:
            dates = [d for d in dates if d <= e]
        if not dates:
            continue
        out.append({
            "start": min(dates),
            "end": max(dates),
            "batch_id": b["batch_id"],
            "status": "invalidated",
            "superseded_by": b.get("superseded_by"),
            "reason": b.get("invalidated_reason"),
        })
    out.sort(key=lambda r: r["start"])
    return out


def _to_date(x) -> dt.date:
    if isinstance(x, dt.datetime):
        return x.date()
    if isinstance(x, dt.date):
        return x
    return dt.date.fromisoformat(str(x)[:10])
