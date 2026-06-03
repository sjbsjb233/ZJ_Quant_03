"""Generic chart explorer (doc §4.6.4 / §6.9) -- table/col/agg -> preview + templates."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from quant_data.schemas import RAW_TABLES, REFERENCE_TABLES, get_spec

from ...db.base import get_db
from ...db.models import ChartTemplate, User
from ...deps import get_current_user, get_reader, heavy_query_slot
from ...errors import ApiError
from ..schemas import ChartTemplateCreate, ExplorerRequest

router = APIRouter(prefix="/explorer", tags=["charts:explorer"],
                   dependencies=[Depends(heavy_query_slot)])

_BROWSEABLE = {**RAW_TABLES, **REFERENCE_TABLES}
_AGG_FN = {"daily_avg": "avg", "weekly_avg": "avg", "monthly_avg": "avg",
           "sum": "sum", "count": "count", "min": "min", "max": "max"}
_BIN = {"daily_avg": "day", "weekly_avg": "week", "monthly_avg": "month"}
_RAW_NO_AGG_CAP = 5000     # agg=none materialises row-by-row → tighten detail-preview cap
_AGG_ROW_CAP = 50000       # aggregated output is itself bounded → cap can be looser


def _ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _safe(v):
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v


@router.post("/preview")
def preview(body: ExplorerRequest, reader=Depends(get_reader), user=Depends(get_current_user)):
    if body.table not in _BROWSEABLE:
        raise ApiError("TABLE_NOT_FOUND", f"表 {body.table} 不可用于 explorer")
    spec = get_spec(body.table)
    valid = set(spec.all_names())
    for col in [body.x_axis, *body.y_axes] + ([body.group_by] if body.group_by else []):
        if col not in valid:
            raise ApiError("INVALID_PARAMETER", f"列 {col} 不在表 {body.table} 中")

    where = []
    for col, vals in (body.filters or {}).items():
        if col in valid and isinstance(vals, list) and vals:
            lits = ",".join("'" + str(v).replace("'", "''") + "'" for v in vals)
            where.append(f"{_ident(col)} IN ({lits})")
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    agg = body.aggregation
    xexpr = (f"date_trunc('{_BIN[agg]}', {_ident(body.x_axis)})"
             if agg in _BIN else _ident(body.x_axis))
    # agg=none materialises every row → trivially pulls a whole big table into
    # memory (the 2026-06 OOM-audit pattern). Refuse unfiltered detail previews of
    # raw (large) tables, and cap row counts by mode.
    if agg == "none":
        if body.table in RAW_TABLES and not where:
            raise ApiError(
                "INVALID_PARAMETER",
                "原始大表的明细预览必须带筛选条件 (filters),或改用聚合模式")
        limit = min(body.limit, _RAW_NO_AGG_CAP)
    else:
        limit = min(body.limit, _AGG_ROW_CAP)

    # Aggregate inside the engine over the de-dup current view -- never
    # materialise the whole table into Python (root cause of the explorer OOM).
    # ``aggregate_current_view`` exposes the current view as relation ``cv``;
    # ``where`` runs post-dedup, exactly as it did on the registered table.
    if body.group_by:
        gcol = _ident(body.group_by)
        y = _ident(body.y_axes[0])
        if agg == "none":
            projection = f"{xexpr} AS x, {gcol} AS g, {y} AS v"
            tail = f"{where_sql} ORDER BY x LIMIT {limit}"
        else:
            projection = f"{xexpr} AS x, {gcol} AS g, {_AGG_FN[agg]}({y}) AS v"
            tail = f"{where_sql} GROUP BY x, g ORDER BY x LIMIT {limit}"
        rows = reader.aggregate_current_view(spec, projection, tail).fetchall()
        xs, seen = [], set()
        for r in rows:
            if r[0] not in seen:
                seen.add(r[0]); xs.append(r[0])
        xs.sort(key=lambda v: (v is None, v))
        idx = {x: i for i, x in enumerate(xs)}
        groups: dict = {}
        for x, g, v in rows:
            groups.setdefault(g, [None] * len(xs))[idx[x]] = v
        series = [{"name": str(g), "data": data} for g, data in groups.items()]
        x_out = [_safe(x) for x in xs]
    else:
        if agg == "none":
            cols = ", ".join(f"{_ident(y)} AS {_ident(y)}" for y in body.y_axes)
            projection = f"{xexpr} AS x, {cols}"
            tail = f"{where_sql} ORDER BY x LIMIT {limit}"
        else:
            cols = ", ".join(f"{_AGG_FN[agg]}({_ident(y)}) AS {_ident(y)}" for y in body.y_axes)
            projection = f"{xexpr} AS x, {cols}"
            tail = f"{where_sql} GROUP BY x ORDER BY x LIMIT {limit}"
        cur = reader.aggregate_current_view(spec, projection, tail)
        names = [d[0] for d in cur.description]
        rows = cur.fetchall()
        x_out = [_safe(r[0]) for r in rows]
        series = [{"name": names[i + 1], "data": [r[i + 1] for r in rows]}
                  for i in range(len(body.y_axes))]
    return {"x": x_out, "series": series, "chart_type": body.chart_type,
            "row_count": len(x_out)}


# --- templates (per user) ---------------------------------------------------
@router.post("/templates")
def save_template(body: ChartTemplateCreate, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    tpl = ChartTemplate(owner=user.username, name=body.name,
                        chart_type=body.chart_type, config=body.config)
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return {"id": tpl.id, "name": tpl.name}


@router.get("/templates")
def list_templates(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = (db.query(ChartTemplate).filter(ChartTemplate.owner == user.username)
            .order_by(ChartTemplate.created_at.desc()).all())
    return {"items": [{"id": t.id, "name": t.name, "chart_type": t.chart_type,
                       "config": t.config, "created_at": t.created_at} for t in rows]}


@router.delete("/templates/{template_id}")
def delete_template(template_id: int, db: Session = Depends(get_db),
                    user: User = Depends(get_current_user)):
    tpl = db.get(ChartTemplate, template_id)
    if tpl is None or tpl.owner != user.username:
        raise ApiError("TEMPLATE_NOT_FOUND", f"模板 {template_id} 不存在")
    db.delete(tpl)
    db.commit()
    return {"id": template_id, "deleted": True}
