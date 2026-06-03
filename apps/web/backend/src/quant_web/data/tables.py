"""Table browser routes (doc §4.5 / §5.2.4) -- raw rows, full view, row lineage."""
from __future__ import annotations

import time

import duckdb
from fastapi import APIRouter, Depends, Query

from quant_data import table_stats
from quant_data.reader import _date_lit, _str_lit, _ts_lit
from quant_data.schemas import RAW_TABLES, REFERENCE_TABLES, TS, get_spec

from ..deps import get_current_user, get_reader, get_root
from ..errors import ApiError
from .serialize import table_payload

router = APIRouter(prefix="/tables", tags=["tables"])

_BROWSEABLE = {**RAW_TABLES, **REFERENCE_TABLES}

# High-cardinality tables must be filtered before browsing. Without symbols or
# a (start, end) window even a paged request makes DuckDB scan the entire
# table; with the cap from P0-2 that "only" fails the query, but for these
# three tables a full scan is never useful interactively either.
_REQUIRE_FILTER = {"raw.bars_daily", "raw.bars_minute", "raw.universe"}

# Fundamentals carries its own PIT ranking (publish_date DESC) -- the default
# dedup picks the latest-INGESTED revision, which can leak a stale value when
# an older revision is backfilled. Keep this in sync with read_fundamentals.
_FUNDAMENTALS_DEDUP = (
    "_src_priority DESC, publish_date DESC, _ingested_at DESC, _batch_id DESC"
)

_NOTES = {
    "raw.bars_daily": "日频行情", "raw.bars_minute": "分钟行情 (实时降采样)",
    "raw.fundamentals": "财务 (PIT 约束)", "raw.universe": "成分/可交易",
    "raw.calendar": "交易日历", "raw.corporate_actions": "公司行动",
    "reference.symbols": "证券字典", "reference.sectors": "行业体系",
}


# --- declarative filter capability contract (方案 §2.1 / §2.2) ---------------
# The backend already supports every one of these params (see _split_filters /
# _current_view_table); this table just *declares* the capability so the
# frontend can render the right control per table instead of hard-coding three
# special cases. ``kind`` drives which control renders (方案 §2.1).
def _symbols() -> dict:
    return {"key": "symbols", "kind": "symbols", "label": "symbols"}


def _date_range(time_col: str = "date") -> dict:
    return {"key": time_col, "kind": "date_range", "label": "日期范围",
            "time_col": time_col}


def _adjust() -> dict:
    return {"key": "adjust", "kind": "adjust", "label": "复权"}


def _freq() -> dict:
    return {"key": "frequency", "kind": "freq", "label": "频率"}


def _as_of() -> dict:
    return {"key": "as_of_date", "kind": "as_of", "label": "as_of_date"}


def _enum(field: str, label: str) -> dict:
    return {"key": field, "kind": "enum", "label": label}


def _bool(field: str, label: str) -> dict:
    return {"key": field, "kind": "bool", "label": label}


def _int(field: str, label: str) -> dict:
    return {"key": field, "kind": "int", "label": label, "min": 1}


_FILTER_SPECS: dict[str, list[dict]] = {
    "raw.bars_daily":        [_symbols(), _date_range("date"), _adjust()],
    "raw.bars_minute":       [_symbols(), _date_range("datetime"), _freq(), _adjust()],
    "raw.universe":          [_symbols(), _date_range("date")],
    "raw.fundamentals":      [_symbols(), _as_of(), _enum("period_type", "报告期类型")],
    "raw.calendar":          [_date_range("date"), _enum("market", "市场")],
    "raw.corporate_actions": [_symbols(), _date_range("action_date"),
                              _enum("action_type", "行动类型")],
    "reference.symbols":     [_symbols(), _enum("exchange", "交易所"),
                              _bool("active_only", "仅在市")],
    "reference.sectors":     [_enum("classification_system", "分类体系"),
                              _int("level", "层级")],
}


def _filters_with_endpoints(name: str) -> list[dict]:
    """Filter specs for ``name``, injecting the enum options endpoint per field."""
    out = []
    for f in _FILTER_SPECS.get(name, []):
        f = dict(f)
        if f["kind"] == "enum":
            f["options_endpoint"] = f"/tables/{name}/enum/{f['key']}"
        out.append(f)
    return out


def _enum_fields(name: str) -> set[str]:
    """White-listed enum columns for ``name`` -- the only columns ``/enum`` will
    run DISTINCT on. Keeps ``symbol`` (high cardinality) off the enum path; the
    symbol dimension is served by the typeahead search endpoint instead."""
    return {f["key"] for f in _FILTER_SPECS.get(name, []) if f["kind"] == "enum"}


# Process-local TTL cache for enum DISTINCT values (方案 §2.3). Low-cardinality
# columns change rarely; a short TTL keeps typeahead/dropdowns near-zero-IO
# without the staleness of a permanent cache.
_ENUM_TTL_S = 60.0
_enum_cache: dict[tuple[str, str], tuple[float, list]] = {}


def _cached_distinct(reader, name: str, field: str) -> list:
    key = (name, field)
    now = time.monotonic()
    hit = _enum_cache.get(key)
    if hit and now - hit[0] < _ENUM_TTL_S:
        return hit[1]
    values = reader.distinct_values(name, field)
    _enum_cache[key] = (now, values)
    return values


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
    time_col = next(
        (c for c in ("date", "datetime", "action_date") if c in business), None
    )
    return {
        "name": name, "kind": spec.kind,
        "primary_key": list(spec.primary_key),
        "partition": [p.name for p in spec.partition],
        "has_extras": spec.has_extras(),
        "columns": cols,
        # ── capability contract (方案 §2.1) — additive, drives the FE renderer ──
        "mutable": spec.mutable,
        "supports_lineage": not spec.mutable,  # only append-only raw has batch lineage
        "lineage_kind": "reference" if spec.mutable else "batch",
        "has_symbol": "symbol" in business,
        "time_col": time_col,
        "require_filter": name in _REQUIRE_FILTER,
        "filters": _filters_with_endpoints(name),
    }


@router.get("/{name}/enum/{field}")
def enum_values(name: str, field: str,
                reader=Depends(get_reader), user=Depends(get_current_user)):
    """Distinct values for a white-listed low-cardinality enum filter column.

    Guarded two ways so it can never be turned into a full-table DISTINCT scan
    of a high-cardinality column (e.g. ``symbol`` on bars): the field must be a
    business column AND must be declared as an ``enum`` filter for this table.
    The symbol dimension is served by the typeahead search endpoint instead.
    """
    spec = _spec_or_404(name)
    if field not in set(spec.business_names()):
        raise ApiError("INVALID_PARAMETER", f"{name} 无字段 {field}")
    if field not in _enum_fields(name):
        raise ApiError("INVALID_PARAMETER", f"{field} 不是 {name} 的可枚举筛选列")
    return {"values": _cached_distinct(reader, name, field)}


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


def _parse_symbols(raw: str | None) -> list[str]:
    """Canonical split for the comma-separated symbols query parameter.

    Used by both _split_filters (predicate construction) and
    _require_filter_for_large_tables (presence check) so that
    ``symbols=`` / ``symbols=,`` / ``symbols= , `` can't bypass the guard
    while producing an empty SQL predicate -- that's the very combination
    that would let an unfiltered large-table scan through.
    """
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


def _split_filters(spec, name: str, p: dict) -> tuple[list[str], list[str]]:
    """Build (pre_filters, post_filters) for SQL pushdown.

    Pre-filters run inside the dwp CTE (before dedup) so they prune source
    rows. Post-filters run after dedup picks a winner -- a row that lost the
    rank to a higher-priority source must never resurface just because the
    caller asked for that source.
    """
    pre: list[str] = []
    cols = set(spec.all_names())
    syms = _parse_symbols(p.get("symbols")) if "symbol" in cols else []
    if syms:
        pre.append("t.symbol IN (" + ",".join(_str_lit(s) for s in syms) + ")")
    time_col = (
        "date" if "date" in cols
        else "datetime" if "datetime" in cols
        else "action_date" if "action_date" in cols
        else None
    )
    if time_col and p.get("start"):
        lit = _ts_lit if spec.type_of(time_col) == TS else _date_lit
        pre.append(f"t.{time_col} >= {lit(p['start'])}")
    if time_col and p.get("end"):
        lit = _ts_lit if spec.type_of(time_col) == TS else _date_lit
        pre.append(f"t.{time_col} <= {lit(p['end'])}")
    if name == "raw.fundamentals" and p.get("as_of_date"):
        pre.append(f"t.publish_date <= {_date_lit(p['as_of_date'])}")
    if name == "raw.fundamentals" and p.get("period_type"):
        pre.append(f"t.period_type = {_str_lit(p['period_type'])}")
    if name == "raw.calendar" and p.get("market"):
        pre.append(f"t.market = {_str_lit(p['market'])}")
    if name == "raw.corporate_actions" and p.get("action_type"):
        pre.append(f"t.action_type = {_str_lit(p['action_type'])}")

    post: list[str] = []
    if p.get("batch_id"):
        post.append(f"_batch_id = {_str_lit(p['batch_id'])}")
    if p.get("source"):
        srcs = [s for s in p["source"].split(",") if s]
        if srcs:
            post.append("_source IN (" + ",".join(_str_lit(s) for s in srcs) + ")")
    return pre, post


def _needs_in_memory_pagination(name: str, p: dict) -> bool:
    """Tables/options that must go through the typed reader + in-memory slice.

    Two distinct reasons (both end up at the same legacy fallback):

    1. **Paging is value-unstable.** ``raw.bars_minute`` calls
       ``_downsample`` which aggregates across minutes -- slicing rows
       before aggregation changes both row count and content. Similarly
       ``raw.bars_daily`` with ``adjust=post`` runs ``first_value()
       OVER (PARTITION BY symbol ORDER BY date)`` -- the normalization
       baseline is the first row of the SQL result, which shifts every
       page.

    2. **The pushdown path bypasses the typed reader.** ``raw.bars_daily``
       with ``adjust=pre`` or ``recompute_adj`` is per-row safe to paginate,
       but the SQL-pushdown route calls ``current_view`` directly and so
       would drop ``_adjust`` / ``_recomputed_factors`` silently. Routing
       these through legacy is the only way the transforms still apply.

    ``reference.symbols`` / ``reference.sectors`` are mutable single-file
    tables that don't go through ``_current_view`` at all; their data is
    small enough that paging in memory is fine.
    """
    if name == "raw.bars_minute":
        return True
    if name == "raw.bars_daily" and (
        (p.get("adjust") or "none") != "none" or p.get("recompute_adj")
    ):
        return True
    if name in ("reference.symbols", "reference.sectors"):
        return True
    return False


def _require_filter_for_large_tables(name: str, p: dict) -> None:
    if name not in _REQUIRE_FILTER:
        return
    # Must mirror what _split_filters actually produces: a raw "symbols=,"
    # parses to an empty list and emits no SQL predicate, so the bool() of
    # the raw string alone would falsely report "filtered" and let the
    # full-table scan through.
    has_symbols = bool(_parse_symbols(p.get("symbols")))
    has_window = bool(p.get("start") and p.get("end"))
    if not (has_symbols or has_window):
        raise ApiError(
            "MISSING_PARAMETER",
            f"浏览大表 {name} 必须指定 symbols 或 start+end 日期范围",
        )


def _require_filter_for_full_view(spec, name: str, p: dict) -> None:
    """Guard the unbounded ``/rows/all`` path (read_full_view has no LIMIT).

    ``/rows`` pushes pagination into SQL and is bounded even without a filter,
    but ``/rows/all`` materialises every physical row of the table. The blanket
    ``_REQUIRE_FILTER`` set only covered bars/universe, leaving fundamentals and
    corporate_actions able to full-scan into the Python heap. Require a bounding
    filter (symbols, or a date window on the table's own time column) for every
    high-cardinality raw table here -- only on the export path, so paged browsing
    of these tables is unchanged.
    """
    if spec.mutable or name == "raw.calendar":
        return  # reference tables + the small static calendar stay open
    cols = set(spec.all_names())
    time_col = ("date" if "date" in cols else "datetime" if "datetime" in cols
                else "action_date" if "action_date" in cols else None)
    has_symbols = bool(_parse_symbols(p.get("symbols"))) if "symbol" in cols else False
    has_window = bool(time_col and p.get("start") and p.get("end"))
    if not (has_symbols or has_window):
        hint = "symbols" + (" 或 start+end 日期范围" if time_col else "")
        raise ApiError("MISSING_PARAMETER", f"导出全量视图 {name} 必须指定 {hint}")


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
    spec = _spec_or_404(name)
    _require_filter_for_large_tables(name, p)

    # Tables/options that can't push pagination into SQL safely (see
    # _needs_in_memory_pagination): fall back to the original typed-reader +
    # in-memory slice path. The P1 forced-filter check above keeps the working
    # set small enough that materialising it is cheap.
    if _needs_in_memory_pagination(name, p):
        tbl = _current_view_table(name, reader, p)
        columns, items = table_payload(tbl)
        return _post_filter_and_page(columns, items, p)

    # SQL-pushdown path: one COUNT for total + one paged read. Never
    # materialises the full table even without any user-supplied filter --
    # fixes the 2026-05-23 OOM where 18.66M rows × ~80 cols were converted to
    # Python dicts just to be sliced by [offset:offset+limit] at the end.
    if name == "raw.fundamentals" and not p.get("as_of_date"):
        raise ApiError("MISSING_PARAMETER", "fundamentals 当前视图必须指定 as_of_date")

    offset = max(0, int(p.get("offset") or 0))
    limit = max(1, min(int(p.get("limit") or 100), 1000))
    pre, post = _split_filters(spec, name, p)
    extras = _parse_extras(p.get("extras_as_columns"))
    dedup_order = _FUNDAMENTALS_DEDUP if name == "raw.fundamentals" else None
    order_by = ", ".join(spec.primary_key)
    try:
        total = reader.count_current_view(
            spec, filters=pre or None, post_filters=post or None,
            dedup_order=dedup_order,
        )
        page = reader.current_view(
            name, filters=pre or None, post_filters=post or None,
            extras_as_columns=extras, order_by=order_by, dedup_order=dedup_order,
            limit=limit, offset=offset,
        )
    except ValueError as e:
        raise ApiError("INVALID_PARAMETER", str(e)) from e
    columns, items = table_payload(page)
    return {
        "columns": columns, "items": items, "total": total,
        "page": offset // limit + 1, "page_size": limit,
    }


@router.get("/{name}/rows/all")
def rows_all(name: str, p: dict = Depends(_all_query_params),
             reader=Depends(get_reader), user=Depends(get_current_user)):
    spec = _spec_or_404(name)
    _require_filter_for_large_tables(name, p)
    _require_filter_for_full_view(spec, name, p)
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
    except duckdb.Error as e:
        # A binder/column error (e.g. a table without _batch_id/_ingested_at)
        # must surface as a readable 4xx, not bubble to the catch-all 500
        # handler -- that was the reference.symbols INTERNAL_ERROR (方案 §0.3 / §5.2).
        raise ApiError("INVALID_PARAMETER", f"{name} 不支持该血缘查询: {e}") from e
    return _serialize_lineage(result)


def _serialize_lineage(result: dict) -> dict:
    from .serialize import jsonify

    return {
        "pk": jsonify(result["pk"]),
        "current": jsonify(result["current"]),
        "history": [jsonify(r) for r in result["history"]],
        "diff_columns": result["diff_columns"],
        "lineage_kind": result.get("lineage_kind", "batch"),
        "mutable": result.get("mutable", False),
    }
