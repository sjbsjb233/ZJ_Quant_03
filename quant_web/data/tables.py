"""Table browser routes (doc §4.5 / §5.2.4) -- raw rows, full view, row lineage."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from quant_data import table_stats
from quant_data.reader import _date_lit, _str_lit, _ts_lit
from quant_data.schemas import RAW_TABLES, REFERENCE_TABLES, TS, get_spec

from ..deps import get_current_user, get_reader, get_root
from ..errors import ApiError
from .serialize import table_payload

router = APIRouter(prefix="/tables", tags=["tables"])

_BROWSEABLE = {**RAW_TABLES, **REFERENCE_TABLES}

_NOTES = {
    "raw.bars_daily": "日频行情", "raw.bars_minute": "分钟行情 (实时降采样)",
    "raw.fundamentals": "财务 (PIT 约束)", "raw.universe": "成分/可交易",
    "raw.calendar": "交易日历", "raw.corporate_actions": "公司行动",
    "reference.symbols": "证券字典", "reference.sectors": "行业体系",
}


def _spec_or_404(name: str):
    if name not in _BROWSEABLE:
        raise ApiError("TABLE_NOT_FOUND", f"表 {name} 不存在或不可浏览")
    return get_spec(name)


def _parse_extras(raw: str | None) -> dict | None:
    if not raw:
        return None
    out = {}
    for part in raw.split(","):
        if ":" in part:
            k, t = part.split(":", 1)
            out[k.strip()] = t.strip()
    return out or None


@router.get("")
def list_tables(root: str = Depends(get_root), user=Depends(get_current_user)):
    stats = {s["table_name"]: s for s in table_stats.get_stats(root=root)}
    out = []
    for name, spec in _BROWSEABLE.items():
        s = stats.get(name, {})
        out.append({
            "name": name,
            "kind": spec.kind,
            "note": _NOTES.get(name),
            "row_count": s.get("row_count"),
            "disk_bytes": s.get("disk_bytes"),
            "file_count": s.get("file_count"),
            "freshness": s.get("freshness_status") or "info",
            "latest_batch_at": s.get("latest_batch_at"),
            "primary_key": list(spec.primary_key),
            "has_extras": spec.has_extras(),
        })
    return {"items": out, "total": len(out)}


@router.get("/{name}/schema")
def schema(name: str, user=Depends(get_current_user)):
    spec = _spec_or_404(name)
    business = set(spec.business_names())
    cols = []
    from quant_data.schemas import DUCK_TYPE_NAME
    for n, t, nullable in spec.columns:
        cols.append({
            "name": n,
            "type": DUCK_TYPE_NAME.get(t, str(t).upper()),
            "nullable": nullable,
            "is_meta": n not in business,
            "is_pk": n in spec.primary_key,
        })
    return {
        "name": name, "kind": spec.kind,
        "primary_key": list(spec.primary_key),
        "partition": [p.name for p in spec.partition],
        "has_extras": spec.has_extras(),
        "columns": cols,
    }


@router.get("/{name}/extras-keys")
def extras_keys(name: str, reader=Depends(get_reader), user=Depends(get_current_user)):
    _spec_or_404(name)
    return {"keys": reader.list_extras_keys(name)}


def _current_view_table(name: str, reader, p: dict):
    """Dispatch to the typed reader for the current view (adjust/PIT/downsample)."""
    extras = _parse_extras(p.get("extras_as_columns"))
    symbols = [s for s in p["symbols"].split(",") if s] if p.get("symbols") else None
    try:
        if name == "raw.bars_daily":
            return reader.read_bars_daily(symbols=symbols, start=p.get("start"), end=p.get("end"),
                                          adjust=p.get("adjust", "none"), extras_as_columns=extras,
                                          recompute_adj=bool(p.get("recompute_adj")))
        if name == "raw.bars_minute":
            return reader.read_bars_minute(symbols=symbols, start=p.get("start"), end=p.get("end"),
                                           frequency=p.get("frequency", "1m"),
                                           adjust=p.get("adjust", "none"), extras_as_columns=extras)
        if name == "raw.fundamentals":
            if not p.get("as_of_date"):
                raise ApiError("MISSING_PARAMETER", "fundamentals 当前视图必须指定 as_of_date")
            tbl = reader.read_fundamentals(symbols=symbols, as_of_date=p["as_of_date"],
                                           extras_as_columns=extras)
            if p.get("period_type"):
                import pyarrow.compute as pc
                tbl = tbl.filter(pc.equal(tbl["period_type"], p["period_type"]))
            return tbl
        if name == "raw.universe":
            return reader.read_universe(symbols=symbols, start=p.get("start"), end=p.get("end"),
                                        extras_as_columns=extras)
        if name == "raw.calendar":
            return reader.read_calendar(market=p.get("market"), start=p.get("start"),
                                        end=p.get("end"))
        if name == "raw.corporate_actions":
            filters = []
            if symbols:
                filters.append("t.symbol IN (" + ",".join(_str_lit(s) for s in symbols) + ")")
            if p.get("start"):
                filters.append(f"t.action_date >= {_date_lit(p['start'])}")
            if p.get("end"):
                filters.append(f"t.action_date <= {_date_lit(p['end'])}")
            if p.get("action_type"):
                filters.append(f"t.action_type = {_str_lit(p['action_type'])}")
            return reader.current_view("raw.corporate_actions", filters=filters or None,
                                       order_by="symbol, action_date")
        if name == "reference.symbols":
            tbl = reader.read_symbols(symbols=symbols, active_only=bool(p.get("active_only")),
                                      extras_as_columns=extras)
            if p.get("exchange"):
                import pyarrow.compute as pc
                tbl = tbl.filter(pc.equal(tbl["exchange"], p["exchange"]))
            return tbl
        if name == "reference.sectors":
            return reader.read_sectors(classification_system=p.get("classification_system"),
                                       level=int(p["level"]) if p.get("level") else None)
    except ValueError as e:
        raise ApiError("INVALID_PARAMETER", str(e)) from e
    raise ApiError("TABLE_NOT_FOUND", f"表 {name} 不可浏览")


def _post_filter_and_page(columns, items, p: dict) -> dict:
    src = {s for s in p["source"].split(",") if s} if p.get("source") else None
    if src:
        items = [r for r in items if r.get("_source") in src]
    if p.get("batch_id"):
        items = [r for r in items if r.get("_batch_id") == p["batch_id"]]
    total = len(items)
    offset = max(0, int(p.get("offset") or 0))
    limit = max(1, min(int(p.get("limit") or 100), 1000))
    return {"columns": columns, "items": items[offset:offset + limit],
            "total": total, "page": offset // limit + 1, "page_size": limit}


def _all_query_params(
    symbols: str | None = None, start: str | None = None, end: str | None = None,
    source: str | None = None, batch_id: str | None = None,
    limit: int = 100, offset: int = 0, extras_as_columns: str | None = None,
    sort: str | None = None, adjust: str = "none", frequency: str = "1m",
    recompute_adj: bool = False, as_of_date: str | None = None,
    period_type: str | None = None, market: str | None = None,
    action_type: str | None = None, active_only: bool = False,
    exchange: str | None = None, classification_system: str | None = None,
    level: int | None = None,
) -> dict:
    return locals()


@router.get("/{name}/rows")
def rows(name: str, p: dict = Depends(_all_query_params),
         reader=Depends(get_reader), user=Depends(get_current_user)):
    _spec_or_404(name)
    tbl = _current_view_table(name, reader, p)
    columns, items = table_payload(tbl)
    return _post_filter_and_page(columns, items, p)


@router.get("/{name}/rows/all")
def rows_all(name: str, p: dict = Depends(_all_query_params),
             reader=Depends(get_reader), user=Depends(get_current_user)):
    spec = _spec_or_404(name)
    if spec.mutable:  # reference tables have no append-only history
        tbl = _current_view_table(name, reader, p)
        columns, items = table_payload(tbl)
        return _post_filter_and_page(columns, items, p)
    filters = _build_sql_filters(spec, p)
    order = ", ".join(spec.primary_key)
    tbl = reader.read_full_view(name, filters=filters or None, order_by=order)
    columns, items = table_payload(tbl)
    return _post_filter_and_page(columns, items, p)


def _build_sql_filters(spec, p: dict) -> list[str]:
    filters = []
    cols = set(spec.all_names())
    if p.get("symbols") and "symbol" in cols:
        syms = [s for s in p["symbols"].split(",") if s]
        if syms:
            filters.append("t.symbol IN (" + ",".join(_str_lit(s) for s in syms) + ")")
    time_col = "date" if "date" in cols else ("datetime" if "datetime" in cols else
               ("action_date" if "action_date" in cols else None))
    if time_col and p.get("start"):
        lit = _ts_lit if spec.type_of(time_col) == TS else _date_lit
        filters.append(f"t.{time_col} >= {lit(p['start'])}")
    if time_col and p.get("end"):
        lit = _ts_lit if spec.type_of(time_col) == TS else _date_lit
        filters.append(f"t.{time_col} <= {lit(p['end'])}")
    if p.get("batch_id"):
        filters.append(f"t._batch_id = {_str_lit(p['batch_id'])}")
    if p.get("source"):
        srcs = [s for s in p["source"].split(",") if s]
        if srcs:
            filters.append("t._source IN (" + ",".join(_str_lit(s) for s in srcs) + ")")
    return filters


@router.get("/{name}/rows/lineage")
def row_lineage(name: str,
                reader=Depends(get_reader), user=Depends(get_current_user),
                symbol: str | None = None, date: str | None = None,
                datetime: str | None = None, report_period: str | None = None,
                period_type: str | None = None, market: str | None = None,
                action_date: str | None = None, action_type: str | None = None,
                code: str | None = None, classification_system: str | None = None,
                sector_id: str | None = None):
    spec = _spec_or_404(name)
    provided = {k: v for k, v in {
        "symbol": symbol, "date": date, "datetime": datetime,
        "report_period": report_period, "period_type": period_type, "market": market,
        "action_date": action_date, "action_type": action_type, "code": code,
        "classification_system": classification_system, "sector_id": sector_id,
    }.items() if v is not None}
    pk = {k: provided[k] for k in spec.primary_key if k in provided}
    missing = [k for k in spec.primary_key if k not in pk]
    if missing:
        raise ApiError("MISSING_PARAMETER",
                       f"行 lineage 需要主键 {list(spec.primary_key)},缺少 {missing}")
    try:
        result = reader.read_row_lineage(name, pk)
    except ValueError as e:
        raise ApiError("INVALID_PARAMETER", str(e)) from e
    return _serialize_lineage(result)


def _serialize_lineage(result: dict) -> dict:
    from .serialize import jsonify

    return {
        "pk": jsonify(result["pk"]),
        "current": jsonify(result["current"]),
        "history": [jsonify(r) for r in result["history"]],
        "diff_columns": result["diff_columns"],
    }
