"""Fundamentals time-series endpoint (doc §4.6.3 / §6.8). PIT-enforced."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends

from quant_data.metadata import field_registry
from quant_data.schemas import FUNDAMENTALS

from ...deps import get_current_user, get_reader, get_root
from ...errors import ApiError
from . import _industry
from ._util import percentile

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
    industry_system: str = "sw",
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

    industry_comparison = None
    if compare_industry:
        industry_comparison = _industry_comparison(
            reader, syms[0], metric_list, as_of_date, period_type, start, end,
            extras, industry_system)

    return {
        "symbols": syms, "metrics": metric_list, "period_type": period_type,
        "as_of_date": as_of_date, "series": series,
        "industry_comparison": industry_comparison,  # VIZ Layer v2 §6.2 / §7.3
    }


def _industry_comparison(reader, primary, metric_list, as_of_date, period_type,
                         start, end, extras, system):
    """Same-industry median series per metric (the dashed 行业参照带, §6.2).

    Industry is the primary (first) symbol's classification node and its level;
    peers are every symbol that rolls up to that same node -- the exact rollup
    used for factor neutralisation (§5.2), so the参照 is口径-consistent. Returns
    one ``{symbol: "__industry_median__", metric, points}`` series per metric.
    """
    st = _industry.load_tree(reader, system)
    prow = reader.read_symbols(symbols=[primary]).to_pylist()
    leaf = prow[0].get("sector_code") if prow else None
    node = st.by_code.get(leaf) if leaf else None
    if node is None or node["level"] is None:
        return None
    level = node["level"]
    peers = [r["symbol"] for r in reader.read_symbols(active_only=False).to_pylist()
             if st.roll(r.get("sector_code"), level) == leaf]
    if not peers:
        return None
    try:
        tbl = reader.read_fundamentals(symbols=peers, as_of_date=as_of_date,
                                       extras_as_columns=extras or None)
    except ValueError:
        return None
    prows = [r for r in tbl.to_pylist() if _period_ok(r, period_type)]
    if start:
        prows = [r for r in prows if r.get("report_period") and r["report_period"] >= start]
    if end:
        prows = [r for r in prows if r.get("report_period") and r["report_period"] <= end]

    series = []
    for m in metric_list:
        by_period: dict = {}
        for r in prows:
            rp, v = r.get("report_period"), r.get(m)
            if rp is not None and v is not None:
                by_period.setdefault(rp, []).append(v)
        points = [{"period": rp, "value": percentile(sorted(vs), 0.5)}
                  for rp, vs in sorted(by_period.items())]
        series.append({"symbol": "__industry_median__", "metric": m, "points": points})
    return {
        "industry": {"code": leaf, "name": st.name(leaf), "level": level},
        "peer_count": len(peers), "series": series,
    }
