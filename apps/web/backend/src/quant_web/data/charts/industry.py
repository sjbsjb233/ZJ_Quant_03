"""行业视图 / Industry Lens endpoints (VIZ Layer v2 §5 / §7.2).

Classification tree + constituents + cross-industry compare + reverse lookup.
All rollups go through :mod:`._industry` (the same algorithm as
``quant_factor.neutralize.industry_at_level``) so 图上行业 == 中性化口径 (§5.2).

Per-symbol metric values come from the PIT current view: market-cap from
``raw.bars_daily`` (latest bar on-or-before ``as_of``), fundamentals from
``read_fundamentals(as_of_date=)`` (publish_date <= as_of). Cross-industry stats
are computed in Python over the bounded per-symbol map -- never the bars table.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends

from quant_data.schemas import FUNDAMENTALS, get_spec

from ...deps import get_current_user, get_reader
from ...errors import ApiError
from . import _industry
from ._util import date_lit, quartiles

router = APIRouter(prefix="/industry", tags=["charts:industry"])

_BARS = "raw.bars_daily"
_FUND_BUSINESS = set(FUNDAMENTALS.business_names())

# metric -> source. bars metrics read from the daily snapshot; fund metrics from
# the PIT fundamentals view (extras get promoted to typed columns on read).
_BARS_METRICS = {"total_mv", "circ_mv", "float_share", "free_float_share", "total_share"}
_FUND_POINT = {"revenue", "net_income", "net_income_attr_parent", "total_assets",
               "total_equity", "eps_basic", "bps", "operating_cash_flow",
               "roe", "gross_margin", "pe_ttm"}
_FUND_DERIVED = {"revenue_yoy", "net_income_yoy"}
_ALL_METRICS = _BARS_METRICS | _FUND_POINT | _FUND_DERIVED

_SYSTEM_LABEL = {"sw": "申万", "sw_2021": "申万 (2021)", "citics": "中信", "gics": "GICS"}
_METRIC_LABEL = {
    "total_mv": "总市值", "circ_mv": "流通市值", "float_share": "流通股本",
    "free_float_share": "自由流通股本", "total_share": "总股本",
    "revenue": "营业收入", "net_income": "净利润", "net_income_attr_parent": "归母净利润",
    "total_assets": "总资产", "total_equity": "股东权益", "eps_basic": "EPS",
    "bps": "每股净资产", "operating_cash_flow": "经营现金流",
    "roe": "ROE", "gross_margin": "毛利率", "pe_ttm": "PE(TTM)",
    "revenue_yoy": "营收增速", "net_income_yoy": "净利增速",
}


def _rows(cursor):
    return cursor.fetchall()


def _resolve_as_of(reader, as_of) -> str:
    if as_of:
        return str(as_of)[:10]
    spec = get_spec(_BARS)
    rows = _rows(reader.aggregate_current_view(spec, "MAX(date) AS d", ""))
    if rows and rows[0][0] is not None:
        return str(rows[0][0])[:10]
    return dt.date.today().isoformat()


def _bars_values(reader, field: str, as_of: str) -> dict[str, float]:
    """{symbol: latest non-null ``field`` on-or-before ``as_of``}."""
    spec = get_spec(_BARS)
    proj = f"symbol, last({field} ORDER BY date) AS v"
    tail = f"WHERE date <= {date_lit(as_of)} AND {field} IS NOT NULL GROUP BY symbol"
    return {r[0]: r[1] for r in _rows(reader.aggregate_current_view(spec, proj, tail)) if r[1] is not None}


def _fund_rows(reader, metric: str, as_of: str, symbols: list[str] | None = None) -> list[dict]:
    extras = {metric: "DOUBLE"} if metric not in _FUND_BUSINESS else None
    try:
        # ``symbols`` prunes the PIT read to a known set (e.g. one industry's
        # members) instead of scanning every symbol's fundamentals into Python.
        tbl = reader.read_fundamentals(symbols=symbols, as_of_date=dt.date.fromisoformat(as_of),
                                       extras_as_columns=extras)
    except ValueError as e:
        raise ApiError("INVALID_PARAMETER", str(e)) from e
    return tbl.to_pylist()


def _fund_point_values(reader, metric: str, as_of: str, symbols: list[str] | None = None) -> dict[str, float]:
    """{symbol: value at the latest report_period visible as of ``as_of``}."""
    best: dict[str, tuple] = {}
    for r in _fund_rows(reader, metric, as_of, symbols):
        rp, v = r.get("report_period"), r.get(metric)
        if rp is None or v is None:
            continue
        sym = r["symbol"]
        if sym not in best or rp > best[sym][0]:
            best[sym] = (rp, v)
    return {s: v for s, (rp, v) in best.items()}


def _fund_yoy_values(reader, base_metric: str, as_of: str) -> dict[str, float]:
    """Year-over-year growth of ``base_metric`` (latest period vs same period -1y)."""
    by_sym: dict[str, dict] = {}
    for r in _fund_rows(reader, base_metric, as_of):
        rp, v = r.get("report_period"), r.get(base_metric)
        if rp is None or v is None:
            continue
        by_sym.setdefault(r["symbol"], {})[rp] = v
    out: dict[str, float] = {}
    for sym, periods in by_sym.items():
        if not periods:
            continue
        latest = max(periods)
        try:
            prev = dt.date(latest.year - 1, latest.month, latest.day)
        except ValueError:
            continue
        pv = periods.get(prev)
        if pv:
            out[sym] = round(periods[latest] / pv - 1, 4)
    return out


def _symbol_metric(reader, metric: str, as_of: str) -> dict[str, float]:
    if metric in _BARS_METRICS:
        return _bars_values(reader, metric, as_of)
    if metric in _FUND_POINT:
        return _fund_point_values(reader, metric, as_of)
    if metric == "revenue_yoy":
        return _fund_yoy_values(reader, "revenue", as_of)
    if metric == "net_income_yoy":
        return _fund_yoy_values(reader, "net_income", as_of)
    raise ApiError("INVALID_PARAMETER", f"未知指标: {metric}")


def _symbol_rows(reader):
    return reader.read_symbols(active_only=False).to_pylist()


# ── ① 行业树 ──────────────────────────────────────────────────────────────────
@router.get("/tree")
def tree(
    system: str = "sw", size_by: str = "count", as_of: str | None = None,
    reader=Depends(get_reader), user=Depends(get_current_user),
):
    if size_by not in ("count", "mv"):
        raise ApiError("INVALID_PARAMETER", "size_by 须为 count / mv")
    st = _industry.load_tree(reader, system)
    syms = _symbol_rows(reader)
    value_of: dict[str, float]
    if size_by == "mv":
        value_of = _bars_values(reader, "total_mv", _resolve_as_of(reader, as_of))
    else:
        value_of = {}

    node_value: dict[str, float] = {c: 0.0 for c in st.by_code}
    covered = 0
    for r in syms:
        code = r.get("sector_code")
        chain = st.ancestors_including(code)
        if not chain:
            continue
        covered += 1
        v = float(value_of.get(r["symbol"], 0.0)) if size_by == "mv" else 1.0
        for anc in chain:
            node_value[anc] = node_value.get(anc, 0.0) + v

    nodes = [
        {"code": c, "name": n["name"], "level": n["level"], "parent": n["parent"],
         "value": node_value.get(c, 0.0)}
        for c, n in st.by_code.items()
    ]
    nodes.sort(key=lambda x: (x["level"] or 0, -(x["value"] or 0), x["code"]))
    return {
        "system": system, "system_label": _SYSTEM_LABEL.get(system, system),
        "size_by": size_by, "nodes": nodes, "levels": st.levels_summary(),
        "coverage": {"covered": covered, "total": len(syms)},
    }


# ── ② 行业成分 ────────────────────────────────────────────────────────────────
@router.get("/composition")
def composition(
    system: str, level: int, code: str, as_of: str | None = None,
    reader=Depends(get_reader), user=Depends(get_current_user),
):
    if level not in (1, 2, 3):
        raise ApiError("INVALID_PARAMETER", "level 须为 1 / 2 / 3")
    as_of = _resolve_as_of(reader, as_of)
    st = _industry.load_tree(reader, system)
    syms = _symbol_rows(reader)
    name_of = {r["symbol"]: r.get("name") for r in syms}

    members = [r["symbol"] for r in syms if st.roll(r.get("sector_code"), level) == code]
    mset = set(members)
    # prune every read to this industry's members -- no whole-table fundamentals
    # scan (the 2026-06 OOM-audit pattern). ``members or [None]`` keeps an empty
    # industry from falling back to "all symbols".
    member_filter = members or ["__none__"]
    mv = {s: v for s, v in _bars_values(reader, "total_mv", as_of).items() if s in mset}
    pe = _fund_point_values(reader, "pe_ttm", as_of, member_filter)
    roe = _fund_point_values(reader, "roe", as_of, member_filter)

    out = [
        {"symbol": s, "name": name_of.get(s), "mv": mv.get(s),
         "pe": pe.get(s), "roe": roe.get(s)}
        for s in members
    ]
    out.sort(key=lambda r: (r["mv"] is None, -(r["mv"] or 0)))
    return {
        "industry": {"code": code, "name": st.name(code), "level": level},
        "members": out, "count": len(out), "as_of": as_of,
        "coverage": {"covered": len(mv), "total": len(out)},
    }


# ── ③ 行业横向对比 / ④ 行业内分布 ─────────────────────────────────────────────
@router.get("/aggregates")
def aggregates(
    system: str, level: int, metric: str = "total_mv", as_of: str | None = None,
    breakdown: str | None = None, code: str | None = None,
    reader=Depends(get_reader), user=Depends(get_current_user),
):
    if level not in (1, 2, 3):
        raise ApiError("INVALID_PARAMETER", "level 须为 1 / 2 / 3")
    if metric not in _ALL_METRICS:
        raise ApiError("INVALID_PARAMETER", f"未知指标: {metric}")
    as_of = _resolve_as_of(reader, as_of)
    st = _industry.load_tree(reader, system)
    syms = _symbol_rows(reader)
    name_of = {r["symbol"]: r.get("name") for r in syms}
    values = _symbol_metric(reader, metric, as_of)
    industry_of, covered, total = _industry.rollup_symbols(st, syms, level)
    meta = {"metric": metric, "metric_label": _METRIC_LABEL.get(metric, metric),
            "level": level, "as_of": as_of,
            "coverage": {"covered": covered, "total": total}}

    # ④ within-industry per-symbol breakdown (distribution / ranking).
    if breakdown == "symbol":
        if not code:
            raise ApiError("MISSING_PARAMETER", "breakdown=symbol 需要 code")
        items = [
            {"symbol": s, "name": name_of.get(s), "value": values.get(s)}
            for s, ind in industry_of.items() if ind == code and values.get(s) is not None
        ]
        items.sort(key=lambda r: -(r["value"] or 0))
        return {**meta, "industry": {"code": code, "name": st.name(code)}, "breakdown": items}

    # ③ cross-industry medians + 25/75 spread.
    buckets: dict[str, list] = {}
    for s, ind in industry_of.items():
        v = values.get(s)
        if v is not None:
            buckets.setdefault(ind, []).append(v)
    industries = []
    for ind, vals in buckets.items():
        q = quartiles(vals)
        industries.append({
            "code": ind, "name": st.name(ind),
            "value": q["p50"], "p25": q["p25"], "p50": q["p50"], "p75": q["p75"],
            "count": q["count"],
        })
    industries.sort(key=lambda r: (r["value"] is None, -(r["value"] or 0)))
    return {**meta, "industries": industries}


# ── ⑤ 个股反查 ────────────────────────────────────────────────────────────────
@router.get("/lookup")
def lookup(
    symbol: str, system: str = "sw",
    reader=Depends(get_reader), user=Depends(get_current_user),
):
    st = _industry.load_tree(reader, system)
    syms = _symbol_rows(reader)
    row = next((r for r in syms if r.get("symbol") == symbol), None)
    if row is None:
        raise ApiError("SYMBOL_NOT_FOUND", f"未找到标的 {symbol}")
    leaf = row.get("sector_code")
    path = st.path(leaf)
    # peers share the same leaf sector_code (closest classification neighbours).
    peers = [
        {"symbol": r["symbol"], "name": r.get("name")}
        for r in syms
        if r.get("sector_code") == leaf and r.get("symbol") != symbol
    ]
    return {
        "symbol": symbol, "name": row.get("name"),
        "system": system, "system_label": _SYSTEM_LABEL.get(system, system),
        "sector_code": leaf, "path": path, "peers": peers,
        "resolved": bool(path),
    }


# ── classification systems (for the 体系 selector) ────────────────────────────
@router.get("/systems")
def systems(reader=Depends(get_reader), user=Depends(get_current_user)):
    counts = _industry.list_systems(reader)
    items = [
        {"value": k, "label": _SYSTEM_LABEL.get(k, k), "count": v}
        for k, v in sorted(counts.items(), key=lambda kv: -kv[1])
    ]
    return {"systems": items, "metrics": [
        {"value": m, "label": _METRIC_LABEL.get(m, m)} for m in
        ["total_mv", "circ_mv", "pe_ttm", "roe", "gross_margin", "revenue", "net_income", "revenue_yoy"]
    ]}
