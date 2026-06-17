"""Shared helpers for the chart routes."""
from __future__ import annotations

import datetime as dt
import math

from ...errors import ApiError


def _fin(value):
    """NaN/±Inf 浮点会序列化成非法 JSON(NaN/Infinity 字面量),前端 fetch().json() 直接报错。
    这里把非有限浮点统一归一为 None,与 data/serialize.jsonify 的处理保持一致。"""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value

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


# --- safe SQL assembly for the aggregate (市场全景 / 行业视图) routes ----------
# These mirror reader._date_lit / _str_lit but stay public to the chart layer so
# the routers never reach into quant_data internals. Every literal is validated /
# escaped: dates parse through ``date.fromisoformat`` and strings double single
# quotes, so the assembled GROUP-BY SQL has no injection surface (doc §8.3).
def parse_date(value) -> dt.date:
    """A validated ``date`` from a date/str; raises a clean 400 ApiError if bad.

    Use this anywhere a query-param date is turned into a Python ``date`` for
    comparisons, so a malformed date returns 400 INVALID_PARAMETER like the rest
    of the chart routes -- never a bare ValueError → 500.
    """
    try:
        return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value)[:10])
    except ValueError as e:
        raise ApiError("INVALID_PARAMETER", f"非法日期: {value!r}") from e


def date_lit(value) -> str:
    """``DATE 'YYYY-MM-DD'`` from a date/str; raises a clean ApiError if bad."""
    return f"DATE '{parse_date(value).isoformat()}'"


def str_lit(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def sym_in_clause(symbols, col: str = "t.symbol") -> str | None:
    """``col IN ('a','b')`` for a bounded symbol set, or None for no filter.

    An explicitly empty set becomes a never-true predicate so a pool with no
    members yields an empty (not full) result -- never silently widens scope.
    """
    if symbols is None:
        return None
    if len(symbols) == 0:
        return "1 = 0"
    return f"{col} IN ({','.join(str_lit(s) for s in symbols)})"


def percentile(sorted_vals: list[float], q: float):
    """Linear-interpolation percentile (q in [0,1]) over a pre-sorted list."""
    n = len(sorted_vals)
    if n == 0:
        return None
    if n == 1:
        return sorted_vals[0]
    pos = q * (n - 1)
    lo = int(pos)
    hi = min(lo + 1, n - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def quartiles(values: list[float]) -> dict:
    """{count, p25, p50, p75} for a metric distribution (None-safe input)."""
    vals = sorted(v for v in values if v is not None)
    return {
        "count": len(vals),
        "p25": percentile(vals, 0.25),
        "p50": percentile(vals, 0.50),
        "p75": percentile(vals, 0.75),
    }


def candle(row: dict, include_extras: bool = False) -> dict:
    time_val = row.get("date") if "date" in row else row.get("datetime")
    ingested = row.get("_ingested_at")
    out = {
        "timestamp": to_ms(time_val),
        "open": _fin(row.get("open")),
        "high": _fin(row.get("high")),
        "low": _fin(row.get("low")),
        "close": _fin(row.get("close")),
        "volume": _fin(row.get("volume")),
        # 市值副图 / 报价头 TURN·MKT.CAP / hover provenance (VIZ Layer v2 §6.1 / §1.2).
        # These ride on the K-line payload (the §1.2 provenance exception) -- never
        # on the pure-analysis market/industry routes.
        "amount": _fin(row.get("amount")),
        "total_mv": _fin(row.get("total_mv")),
        "circ_mv": _fin(row.get("circ_mv")),
        "turnover": _fin(row.get("turnover")),
        "_batch_id": row.get("_batch_id"),
        "_source": row.get("_source"),
        "_ingested_at": ingested.isoformat() if hasattr(ingested, "isoformat") else ingested,
    }
    if include_extras:
        ex = row.get("_extras")
        if isinstance(ex, list):  # arrow MAP cell
            ex = {str(k): v for k, v in ex}
        out["_extras"] = ex
    return out
