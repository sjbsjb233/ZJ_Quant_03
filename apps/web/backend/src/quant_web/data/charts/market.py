"""市场全景 / Market Overview endpoints (VIZ Layer v2 §4 / §7.1).

Full-market cross-section + breadth over ``raw.universe`` + ``raw.bars_daily``
market-cap columns + ``reference.symbols``. Every cross-section panel takes its
``as_of`` current view so no future value ever leaks back into a historical day
(§8.1 PIT). Heavy tables (~5000 symbols/day) are aggregated **inside DuckDB** via
``reader.aggregate_current_view`` -- only the small grouped result returns to
Python (§7 / §8.3). Reference dictionaries are small and read whole.

Responses carry only business columns -- never ``_batch_id`` / ``_source`` /
``_extras`` (§8.5: the physical boundary that keeps 可视化 ≠ 表浏览).
"""
from __future__ import annotations

import math
from collections import Counter

from fastapi import APIRouter, Depends

from quant_data import ColumnNotInViewError
from quant_data.schemas import get_spec

from ...deps import get_current_user, get_reader, heavy_query_slot
from ...errors import ApiError
from ._util import date_lit, parse_date, quartiles, sym_in_clause

router = APIRouter(prefix="/market", tags=["charts:market"],
                   dependencies=[Depends(heavy_query_slot)])

_BARS = "raw.bars_daily"
_UNIVERSE = "raw.universe"

# index pool -> the universe boolean column that defines membership.
_POOL_COL = {"hs300": "in_hs300", "zz500": "in_zz500", "zz1000": "in_zz1000", "sse50": "in_sse50"}
_MV_FIELDS = {"total_mv", "circ_mv"}

# board / exchange code -> human label (matches the design's 市场结构 panel).
_BOARD_LABEL = {
    "main": "主板", "gem": "创业板", "star": "科创板", "kcb": "科创板",
    "bse": "北交所", "neeq": "新三板",
}
_EXCHANGE_LABEL = {"SH": "上交所", "SZ": "深交所", "BJ": "北交所",
                   "SSE": "上交所", "SZSE": "深交所", "BSE": "北交所"}


def _rows(cursor) -> tuple[list[str], list[tuple]]:
    return [d[0] for d in cursor.description], cursor.fetchall()


def _range_filters(start, end) -> list[str]:
    out = []
    if start:
        out.append(f"t.date >= {date_lit(start)}")
    if end:
        out.append(f"t.date <= {date_lit(end)}")
    return out


def _pool_symbols(reader, pool: str | None, as_of) -> list[str] | None:
    """Resolve an index pool to its member symbols as of ``as_of`` (None=全市场).

    Membership is read from the universe current view at a single date; passing
    the result as a bounded ``symbol IN (...)`` filter keeps the downstream
    aggregation pruned without a cross-table join. None means no filter.
    """
    if not pool or pool in ("all", "全市场"):
        return None
    col = _POOL_COL.get(pool)
    if col is None:
        raise ApiError("INVALID_PARAMETER", f"未知指数池: {pool}")
    spec = get_spec(_UNIVERSE)
    tail = f"WHERE {col} GROUP BY symbol ORDER BY symbol"
    filters = [f"t.date = {date_lit(as_of)}"] if as_of else None
    _, rows = _rows(reader.aggregate_current_view(spec, "symbol", tail, filters))
    return [r[0] for r in rows]


def _resolve_as_of(reader, as_of, table=_UNIVERSE):
    """Latest trading day on-or-before ``as_of`` (or the latest overall if None).

    The frontend always sends ``as_of = today``; on a weekend / holiday / before
    that day's bars land there is no row exactly on ``as_of``, so a single-day
    snapshot must snap back to the most recent real trading day (``date <= as_of``)
    rather than show an empty panel. Mirrors industry._bars_values' on-or-before
    semantics so the two pages agree on "the current cross-section".
    """
    # O(metadata) latest date on-or-before -- no full-table de-dup window scan (P0 2026-06-01).
    d = reader.latest_date_in_view(table, on_or_before=parse_date(as_of) if as_of else None)
    return d.isoformat() if d is not None else None


# ── ① 涨跌广度 ────────────────────────────────────────────────────────────────
@router.get("/breadth")
def breadth(
    start: str | None = None, end: str | None = None, pool: str | None = None,
    reader=Depends(get_reader), user=Depends(get_current_user),
):
    pool_syms = _pool_symbols(reader, pool, end or start)
    base = _range_filters(start, end)
    if (c := sym_in_clause(pool_syms)):
        base.append(c)

    bars = get_spec(_BARS)
    proj = (
        "CAST(date AS DATE) AS d, "
        "SUM(CASE WHEN close > pre_close THEN 1 ELSE 0 END) AS adv, "
        "SUM(CASE WHEN close < pre_close THEN 1 ELSE 0 END) AS dec, "
        "SUM(CASE WHEN close = pre_close THEN 1 ELSE 0 END) AS unch"
    )
    _, brows = _rows(reader.aggregate_current_view(
        bars, proj, "WHERE pre_close IS NOT NULL GROUP BY 1 ORDER BY 1", base or None))

    # Honest coverage over the window: distinct symbols with a usable pre_close
    # (those that can contribute to breadth) vs all distinct symbols seen. Can
    # legitimately read <100% -> the badge标红, never a hardcoded full bar (§8.4).
    cov_proj = ("COUNT(DISTINCT symbol) AS total, "
                "COUNT(DISTINCT CASE WHEN pre_close IS NOT NULL THEN symbol END) AS covered")
    _, crows = _rows(reader.aggregate_current_view(bars, cov_proj, "", base or None))
    cov_total = int(crows[0][0] or 0) if crows else 0
    cov_covered = int(crows[0][1] or 0) if crows else 0

    uni = get_spec(_UNIVERSE)
    uproj = (
        "CAST(date AS DATE) AS d, "
        "SUM(CASE WHEN limit_up_status = 'limit_up' THEN 1 ELSE 0 END) AS lu, "
        "SUM(CASE WHEN limit_up_status = 'limit_down' THEN 1 ELSE 0 END) AS ld"
    )
    ufilt = _range_filters(start, end)
    if (c := sym_in_clause(pool_syms)):
        ufilt.append(c)
    _, urows = _rows(reader.aggregate_current_view(uni, uproj, "GROUP BY 1 ORDER BY 1", ufilt or None))
    lim = {str(r[0]): (int(r[1] or 0), int(r[2] or 0)) for r in urows}

    dates, adv, dec, unch, lu, ld = [], [], [], [], [], []
    for r in brows:
        d = str(r[0])
        dates.append(d)
        adv.append(int(r[1] or 0)); dec.append(int(r[2] or 0)); unch.append(int(r[3] or 0))
        u = lim.get(d, (0, 0))
        lu.append(u[0]); ld.append(u[1])
    return {
        "dates": dates, "advancers": adv, "decliners": dec, "unchanged": unch,
        "limit_up": lu, "limit_down": ld,
        "pool": pool or "all", "coverage": {"covered": cov_covered, "total": cov_total},
    }


# ── ② 指数成分覆盖 ────────────────────────────────────────────────────────────
@router.get("/index-membership")
def index_membership(
    start: str | None = None, end: str | None = None,
    reader=Depends(get_reader), user=Depends(get_current_user),
):
    spec = get_spec(_UNIVERSE)
    proj = (
        "CAST(date AS DATE) AS d, "
        "SUM(CASE WHEN in_hs300 THEN 1 ELSE 0 END) AS hs300, "
        "SUM(CASE WHEN in_zz500 THEN 1 ELSE 0 END) AS zz500, "
        "SUM(CASE WHEN in_zz1000 THEN 1 ELSE 0 END) AS zz1000, "
        "SUM(CASE WHEN in_sse50 THEN 1 ELSE 0 END) AS sse50"
    )
    cols, rows = _rows(reader.aggregate_current_view(
        spec, proj, "GROUP BY 1 ORDER BY 1", _range_filters(start, end) or None))
    dates = [str(r[0]) for r in rows]
    series = {k: [int(r[i + 1] or 0) for r in rows] for i, k in enumerate(["hs300", "zz500", "zz1000", "sse50"])}
    return {"dates": dates, "series": series}


# ── ③ 交易状态占比 ────────────────────────────────────────────────────────────
@router.get("/status")
def status(
    start: str | None = None, end: str | None = None, pool: str | None = None,
    reader=Depends(get_reader), user=Depends(get_current_user),
):
    filt = _range_filters(start, end)
    if (c := sym_in_clause(_pool_symbols(reader, pool, end or start))):
        filt.append(c)
    spec = get_spec(_UNIVERSE)
    proj = (
        "CAST(date AS DATE) AS d, "
        "SUM(CASE WHEN is_tradable THEN 1 ELSE 0 END) AS tradable, "
        "SUM(CASE WHEN is_suspended THEN 1 ELSE 0 END) AS suspended, "
        "SUM(CASE WHEN st_status IS NOT NULL AND st_status <> 'normal' THEN 1 ELSE 0 END) AS st, "
        "COUNT(*) AS total"
    )
    _, rows = _rows(reader.aggregate_current_view(spec, proj, "GROUP BY 1 ORDER BY 1", filt or None))
    return {
        "dates": [str(r[0]) for r in rows],
        "tradable": [int(r[1] or 0) for r in rows],
        "suspended": [int(r[2] or 0) for r in rows],
        "st": [int(r[3] or 0) for r in rows],
        "total": [int(r[4] or 0) for r in rows],
        "pool": pool or "all",
    }


# ── ④ 市值分布 (当日快照) ─────────────────────────────────────────────────────
@router.get("/mv-distribution")
def mv_distribution(
    as_of: str | None = None, field: str = "total_mv", bins: int = 30, pool: str | None = None,
    reader=Depends(get_reader), user=Depends(get_current_user),
):
    if field not in _MV_FIELDS:
        raise ApiError("INVALID_PARAMETER", f"市值字段须为 {sorted(_MV_FIELDS)}")
    bins = max(5, min(int(bins), 100))
    as_of = _resolve_as_of(reader, as_of, _BARS)
    if as_of is None:
        return {"bins": [], "p10": None, "p50": None, "p90": None, "field": field,
                "log_scale": True, "as_of": None, "coverage": {"covered": 0, "total": 0}}

    spec = get_spec(_BARS)
    filt = [f"t.date = {date_lit(as_of)}"]
    if (c := sym_in_clause(_pool_symbols(reader, pool, as_of))):
        filt.append(c)

    stat_proj = (
        f"COUNT(*) AS total, COUNT({field}) AS cnt, MIN({field}) AS lo, MAX({field}) AS hi, "
        f"quantile_cont({field}, 0.1) AS p10, quantile_cont({field}, 0.5) AS p50, "
        f"quantile_cont({field}, 0.9) AS p90"
    )
    try:
        _, srows = _rows(reader.aggregate_current_view(spec, stat_proj, "", filt))
    except ColumnNotInViewError:
        # 整个面板就是市值分布;市值列未入库时没有可降级的内容,给明确提示而非 500。
        raise ApiError(
            "DATA_UNAVAILABLE",
            f"市值数据暂未入库（{_BARS} 缺少 {field} 列),请补采市值数据后重试",
            details={"metric": field, "table": _BARS},
        ) from None
    total, cnt, lo, hi, p10, p50, p90 = srows[0] if srows else (0, 0, None, None, None, None, None)
    total, cnt = int(total or 0), int(cnt or 0)
    coverage = {"covered": cnt, "total": total}
    if cnt == 0 or lo is None or hi is None or lo <= 0 or hi <= lo:
        return {"bins": [], "p10": p10, "p50": p50, "p90": p90, "field": field,
                "log_scale": True, "as_of": as_of, "coverage": coverage}

    # log-scale histogram: bucket on ln(value) so the long tail is legible.
    ln_lo, ln_hi = math.log(lo), math.log(hi)
    step = (ln_hi - ln_lo) / bins
    hist_proj = (
        f"LEAST(CAST(FLOOR((ln({field}) - {ln_lo}) / {step}) AS INTEGER), {bins - 1}) AS b, "
        "COUNT(*) AS c"
    )
    _, hrows = _rows(reader.aggregate_current_view(
        spec, hist_proj, f"WHERE {field} IS NOT NULL GROUP BY 1 ORDER BY 1", filt))
    counts = {int(r[0]): int(r[1]) for r in hrows if r[0] is not None}
    out_bins = []
    for i in range(bins):
        out_bins.append({
            "lo": math.exp(ln_lo + i * step),
            "hi": math.exp(ln_lo + (i + 1) * step),
            "count": counts.get(i, 0),
        })
    return {"bins": out_bins, "p10": p10, "p50": p50, "p90": p90, "field": field,
            "log_scale": True, "as_of": as_of, "coverage": coverage}


# ── ⑤ 市场结构 (交易所 × 板块, 当日快照) ──────────────────────────────────────
@router.get("/composition")
def composition(
    as_of: str | None = None, reader=Depends(get_reader), user=Depends(get_current_user),
):
    as_of_d = parse_date(as_of) if as_of else None
    rows = reader.read_symbols(active_only=False).to_pylist()
    counter: Counter = Counter()
    total = 0
    for r in rows:
        ld = r.get("list_date")
        dd = r.get("delist_date")
        if as_of_d is not None:
            if ld is not None and ld > as_of_d:
                continue  # not yet listed as of the snapshot
            if dd is not None and dd <= as_of_d:
                continue  # already delisted
        elif not r.get("is_active"):
            continue
        ex = r.get("exchange") or "—"
        bd = r.get("board") or "—"
        counter[(ex, bd)] += 1
        total += 1
    tree = [
        {
            "exchange": ex, "exchange_label": _EXCHANGE_LABEL.get(ex, ex),
            "board": bd, "board_label": _BOARD_LABEL.get(bd, bd),
            "count": n,
        }
        for (ex, bd), n in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    # coverage stays explicit like every other snapshot panel (§8.4): symbols with
    # a known exchange & board are "covered"; "—" placeholders are not.
    covered = sum(n for (ex, bd), n in counter.items() if ex != "—" and bd != "—")
    return {"tree": tree, "total": total, "as_of": as_of_d.isoformat() if as_of_d else None,
            "coverage": {"covered": covered, "total": total}}


# ── ⑥ 新股节奏 ────────────────────────────────────────────────────────────────
@router.get("/new-listings")
def new_listings(
    start: str | None = None, end: str | None = None, bucket: str = "month",
    reader=Depends(get_reader), user=Depends(get_current_user),
):
    if bucket not in ("month", "year"):
        raise ApiError("INVALID_PARAMETER", "bucket 须为 month / year")
    start_d = parse_date(start) if start else None
    end_d = parse_date(end) if end else None
    counter: Counter = Counter()
    for r in reader.read_symbols(active_only=False).to_pylist():
        ld = r.get("list_date")
        if ld is None:
            continue
        if start_d and ld < start_d:
            continue
        if end_d and ld > end_d:
            continue
        key = f"{ld.year:04d}-{ld.month:02d}" if bucket == "month" else f"{ld.year:04d}"
        counter[key] += 1
    periods = sorted(counter)
    return {"periods": periods, "counts": [counter[p] for p in periods], "bucket": bucket}
