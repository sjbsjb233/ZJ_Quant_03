"""Shared helpers for the chart routes."""
from __future__ import annotations

import datetime as dt

_EPOCH = dt.datetime(1970, 1, 1)

_DAILY_FREQ = {"1d", "1w", "1M", "1D", "1W"}


def to_ms(value) -> int:
    """Epoch milliseconds (UTC) for a date/datetime -- KLineChart's timestamp."""
    if isinstance(value, dt.datetime):
        d = value
    elif isinstance(value, dt.date):
        d = dt.datetime(value.year, value.month, value.day)
    else:
        d = dt.datetime.fromisoformat(str(value))
    return int((d - _EPOCH).total_seconds() * 1000)


def table_for_frequency(frequency: str) -> str:
    return "raw.bars_daily" if frequency in _DAILY_FREQ else "raw.bars_minute"


def candle(row: dict, include_extras: bool = False) -> dict:
    time_val = row.get("date") if "date" in row else row.get("datetime")
    out = {
        "timestamp": to_ms(time_val),
        "open": row.get("open"),
        "high": row.get("high"),
        "low": row.get("low"),
        "close": row.get("close"),
        "volume": row.get("volume"),
        "_batch_id": row.get("_batch_id"),
        "_source": row.get("_source"),
    }
    if include_extras:
        ex = row.get("_extras")
        if isinstance(ex, list):  # arrow MAP cell
            ex = {str(k): v for k, v in ex}
        out["_extras"] = ex
    return out
