"""Fundamentals time-series endpoint (doc §4.6.3 / §6.8). PIT-enforced."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends

from quant_data.metadata import field_registry
from quant_data.schemas import FUNDAMENTALS

from ...deps import get_current_user, get_reader, get_root
from ...errors import ApiError

router = APIRouter(prefix="/fundamentals", tags=["charts:fundamentals"])

_BUSINESS = set(FUNDAMENTALS.business_names())


def _period_ok(row: dict, period_type: str | None) -> bool:
    if not period_type:
        return True
    pt = (row.get("period_type") or "").upper()
    if period_type == "annual":
        rp = row.get("report_period")
        return pt in ("A", "ANNUAL", "FY", "Y") or (rp is not None and rp.month == 12)
    if period_type == "quarterly":
        return pt.startswith("Q") or pt in ("ANNUAL", "A")
    return True  # ttm: no resampling this period (doc §4.6.3 derivatives are P1-ish)


@router.get("")
def fundamentals(
    symbols: str, metrics: str, as_of_date: dt.date,
    reader=Depends(get_reader), root: str = Depends(get_root), user=Depends(get_current_user),
    period_type: str = "annual", start: dt.date | None = None, end: dt.date | None = None,
    derivatives: str | None = None, compare_industry: bool = False,
):
    syms = [s for s in symbols.split(",") if s]
    metric_list = [m for m in metrics.split(",") if m]
    if not syms or not metric_list:
        raise ApiError("MISSING_PARAMETER", "symbols 与 metrics 必填")
    extras = {m: "DOUBLE" for m in metric_list if m not in _BUSINESS}
    try:
        tbl = reader.read_fundamentals(symbols=syms, as_of_date=as_of_date,
                                       extras_as_columns=extras or None)
    except ValueError as e:
        raise ApiError("INVALID_PARAMETER", str(e)) from e

    rows = [r for r in tbl.to_pylist() if _period_ok(r, period_type)]
    if start:
        rows = [r for r in rows if r.get("report_period") and r["report_period"] >= start]
    if end:
        rows = [r for r in rows if r.get("report_period") and r["report_period"] <= end]

    want = {x.strip() for x in derivatives.split(",")} if derivatives else set()
    units = {}
    for m in metric_list:
        f = field_registry.get_field("raw.fundamentals", m, root=root)
        units[m] = f.get("unit") if f else None

    series = []
    for sym in syms:
        srows = sorted((r for r in rows if r.get("symbol") == sym),
                       key=lambda r: r.get("report_period") or dt.date.min)
        by_period = {r["report_period"]: r for r in srows}
        for m in metric_list:
            points = []
            for i, r in enumerate(srows):
                rp = r["report_period"]
                val = r.get(m)
                yoy = qoq = None
                if "yoy" in want and val is not None:
                    prev = by_period.get(dt.date(rp.year - 1, rp.month, rp.day))
                    if prev and prev.get(m):
                        yoy = round(val / prev[m] - 1, 4)
                if "qoq" in want and val is not None and i > 0:
                    pv = srows[i - 1].get(m)
                    if pv:
                        qoq = round(val / pv - 1, 4)
                points.append({"period": rp, "value": val, "yoy": yoy, "qoq": qoq})
            series.append({"symbol": sym, "metric": m, "unit": units.get(m), "points": points})

    return {
        "symbols": syms, "metrics": metric_list, "period_type": period_type,
        "as_of_date": as_of_date, "series": series,
        "industry_comparison": None,  # doc §11 open-q 12: P1
    }
