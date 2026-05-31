"""Universe (stock-pool) endpoints — list / create / update / delete /
preview + symbol search + industry listing."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import polars as pl
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from quant_data import DataReader
from quant_data import logging as qd_logging
from quant_factor.universe import resolve_universe

from ..db.models import FactorRecord, Universe, UniverseMember
from ..deps import current_user, get_db, get_reader, require_editor
from ..errors import ApiError


router = APIRouter()

_qlog = qd_logging.get_logger("factor.universe")


def _register_specific_universe_routes() -> None:
    """Register the specific /universe/symbols/search and /universe/industries
    routes BEFORE the /universe/{universe_id} catch-all so FastAPI picks them
    correctly. Called at the bottom of this module."""
    router.add_api_route("/universe/symbols/search", search_symbols, methods=["GET"])
    router.add_api_route("/universe/industries", list_industries, methods=["GET"])


def _serialize_universe(u: Universe, *, member_count: int | None = None, used_by: int = 0) -> dict[str, Any]:
    return {
        "universe_id": u.universe_id,
        "name": u.name,
        "kind": u.kind,
        "preset_flag": u.preset_flag,
        "classification_system": u.classification_system,
        "default_industry_level": u.default_industry_level,
        "snapshot_retention_days": u.snapshot_retention_days,
        "exclude_st": u.exclude_st,
        "exclude_suspended": u.exclude_suspended,
        "min_list_days": u.min_list_days,
        "description": u.description,
        "owner": u.owner,
        "member_count": member_count,
        "used_by": used_by,
        "is_preset": u.kind == "preset_index",
        "created_at": u.created_at.isoformat() + "Z" if u.created_at else None,
        "updated_at": u.updated_at.isoformat() + "Z" if u.updated_at else None,
    }


def _used_by_count(db: Session, universe_id: str) -> int:
    return (
        db.query(FactorRecord)
        .filter(FactorRecord.tracking_universe_id == universe_id)
        .count()
    )


@router.get("/universe")
def list_universe(
    db: Session = Depends(get_db),
    user=Depends(current_user),
) -> dict:
    rows = db.query(Universe).order_by(Universe.kind.asc(), Universe.created_at.asc()).all()
    items = []
    for u in rows:
        mc = None
        if u.kind == "custom":
            mc = (
                db.query(func.count(UniverseMember.id))
                .filter(UniverseMember.universe_id == u.universe_id)
                .scalar()
            )
        items.append(
            _serialize_universe(u, member_count=mc, used_by=_used_by_count(db, u.universe_id))
        )
    return {"items": items}


@router.get("/universe/{universe_id}")
def get_universe(
    universe_id: str,
    db: Session = Depends(get_db),
    user=Depends(current_user),
) -> dict:
    u = db.get(Universe, universe_id)
    if u is None:
        raise ApiError("UNIVERSE_NOT_FOUND", f"票池 {universe_id} 不存在")
    members = (
        [
            r[0]
            for r in db.query(UniverseMember.symbol)
            .filter(UniverseMember.universe_id == universe_id)
            .all()
        ]
        if u.kind == "custom"
        else []
    )
    return {
        **_serialize_universe(u, member_count=len(members), used_by=_used_by_count(db, universe_id)),
        "members": members,
    }


@router.post("/universe")
def create_universe(
    body: dict[str, Any],
    db: Session = Depends(get_db),
    user=Depends(require_editor),
) -> dict:
    name = (body.get("name") or "").strip()
    if not name:
        raise ApiError("INVALID_PARAMETER", "name 必填")
    symbols = list(body.get("symbols") or [])
    kind = body.get("kind") or "custom"
    if kind == "preset_index":
        raise ApiError("INVALID_PARAMETER", "预设票池不能由用户创建")
    if not symbols:
        raise ApiError("INVALID_PARAMETER", "自定义票池需要至少 1 只股票")
    uid = body.get("universe_id") or f"cust_{uuid.uuid4().hex[:8]}"
    if db.get(Universe, uid):
        raise ApiError("ALREADY_EXISTS", f"票池 {uid} 已存在")

    u = Universe(
        universe_id=uid,
        name=name,
        kind="custom",
        classification_system=body.get("classification_system") or "SW",
        default_industry_level=int(body.get("default_industry_level") or 1),
        snapshot_retention_days=int(body.get("snapshot_retention_days") or 730),
        exclude_st=bool(body.get("exclude_st", True)),
        exclude_suspended=bool(body.get("exclude_suspended", True)),
        min_list_days=int(body.get("min_list_days") or 60),
        description=body.get("description") or "",
        owner=user.username,
    )
    db.add(u)
    db.commit()
    # de-dup symbols
    with qd_logging.timed(_qlog, "universe.build", universe_id=uid) as m:
        seen: set[str] = set()
        for sym in symbols:
            if sym in seen:
                continue
            seen.add(sym)
            db.add(UniverseMember(universe_id=uid, symbol=sym))
        db.commit()
        m["n_symbols"] = len(seen)
    return _serialize_universe(u, member_count=len(seen), used_by=0)


@router.patch("/universe/{universe_id}")
def update_universe(
    universe_id: str,
    body: dict[str, Any],
    db: Session = Depends(get_db),
    user=Depends(require_editor),
) -> dict:
    u = db.get(Universe, universe_id)
    if u is None:
        raise ApiError("UNIVERSE_NOT_FOUND", f"票池 {universe_id} 不存在")
    if u.kind == "preset_index":
        raise ApiError("PERMISSION_DENIED", "预设票池不可编辑")
    for k in (
        "name",
        "exclude_st",
        "exclude_suspended",
        "min_list_days",
        "default_industry_level",
        "classification_system",
        "description",
    ):
        if k in body:
            setattr(u, k, body[k])
    if "symbols" in body:
        with qd_logging.timed(_qlog, "universe.build", universe_id=universe_id) as m:
            syms = list(body["symbols"])
            db.query(UniverseMember).filter(
                UniverseMember.universe_id == universe_id
            ).delete()
            for s in syms:
                db.add(UniverseMember(universe_id=universe_id, symbol=s))
            m["n_symbols"] = len(syms)
    db.commit()
    db.refresh(u)
    mc = (
        db.query(func.count(UniverseMember.id))
        .filter(UniverseMember.universe_id == universe_id)
        .scalar()
    )
    return _serialize_universe(u, member_count=mc, used_by=_used_by_count(db, universe_id))


@router.delete("/universe/{universe_id}")
def delete_universe(
    universe_id: str,
    db: Session = Depends(get_db),
    user=Depends(require_editor),
) -> dict:
    u = db.get(Universe, universe_id)
    if u is None:
        raise ApiError("UNIVERSE_NOT_FOUND", f"票池 {universe_id} 不存在")
    if u.kind == "preset_index":
        raise ApiError("PERMISSION_DENIED", "预设票池不可删除")
    db.query(UniverseMember).filter(
        UniverseMember.universe_id == universe_id
    ).delete()
    db.delete(u)
    db.commit()
    return {"universe_id": universe_id, "deleted": True}


@router.post("/universe/{universe_id}/preview")
def preview_universe(
    universe_id: str,
    body: dict[str, Any],
    db: Session = Depends(get_db),
    reader: DataReader = Depends(get_reader),
    user=Depends(current_user),
) -> dict:
    """Preview the resolved members + breakdown of which filter step removed
    how many symbols on a given date (design §19.2-D)."""
    u = db.get(Universe, universe_id)
    if u is None:
        raise ApiError("UNIVERSE_NOT_FOUND", f"票池 {universe_id} 不存在")
    date_str = body.get("date") or dt.date.today().isoformat()
    d = dt.date.fromisoformat(date_str)

    # Baseline universe row on that date — no overlay filters applied yet
    members = (
        [
            r[0]
            for r in db.query(UniverseMember.symbol)
            .filter(UniverseMember.universe_id == universe_id)
            .all()
        ]
        if u.kind == "custom"
        else None
    )

    base_def = {
        "universe_id": universe_id,
        "kind": u.kind,
        "preset_flag": u.preset_flag,
        "classification_system": u.classification_system,
        "exclude_st": False,
        "exclude_suspended": False,
        "min_list_days": 0,
    }
    base = resolve_universe(reader, base_def, members, d, d)
    selected = base.height

    # Each overlay applied incrementally
    step1_def = dict(base_def, exclude_st=u.exclude_st)
    s1 = resolve_universe(reader, step1_def, members, d, d).height
    excluded_st = selected - s1

    step2_def = dict(step1_def, exclude_suspended=u.exclude_suspended)
    s2 = resolve_universe(reader, step2_def, members, d, d).height
    excluded_suspended = s1 - s2

    step3_def = dict(step2_def, min_list_days=u.min_list_days)
    s3 = resolve_universe(reader, step3_def, members, d, d).height
    excluded_newly_listed = s2 - s3

    final = s3
    coverage = round(final / selected, 4) if selected else 0.0

    # Pull names for the first 25 selected
    syms = (
        s3 if isinstance(s3, list) else (
            resolve_universe(reader, step3_def, members, d, d)["symbol"].head(25).to_list()
        )
    )
    syms_list = (
        resolve_universe(reader, step3_def, members, d, d)["symbol"]
        .head(25)
        .to_list()
    )
    syms_ref = pl.from_arrow(reader.read_symbols(symbols=syms_list))
    name_map = {row["symbol"]: row.get("name") for row in syms_ref.to_dicts()}
    sample = [
        {"symbol": s, "name": name_map.get(s) or ""} for s in syms_list
    ]

    _qlog.info("universe.preview", universe_id=universe_id, date=date_str,
               selected=selected, tradable=final, coverage=coverage)
    return {
        "universe_id": universe_id,
        "date": date_str,
        "selected": selected,
        "excluded_st": excluded_st,
        "excluded_suspended": excluded_suspended,
        "excluded_newly_listed": excluded_newly_listed,
        "tradable": final,
        "coverage": coverage,
        "sample": sample,
    }


def search_symbols(
    q: str = Query(""),
    limit: int = 20,
    reader: DataReader = Depends(get_reader),
    user=Depends(current_user),
) -> dict:
    """Live symbol-completion: matches against reference.symbols by symbol
    or name (prefix / contains)."""
    if not q or not q.strip():
        return {"items": []}
    syms = pl.from_arrow(reader.read_symbols(active_only=True))
    if syms.height == 0:
        return {"items": []}
    ql = q.strip().lower()
    hit = (
        syms.with_columns(
            [
                pl.col("symbol").str.to_lowercase().alias("_sl"),
                pl.col("name").str.to_lowercase().alias("_nl"),
            ]
        )
        .filter(
            pl.col("_sl").str.contains(ql, literal=True)
            | pl.col("_nl").str.contains(ql, literal=True)
        )
        .head(limit)
        .select(["symbol", "name", "exchange"])
    )
    return {"items": hit.to_dicts()}


def list_industries(
    system: str = Query("SW"),
    level: int = Query(1, ge=1, le=3),
    reader: DataReader = Depends(get_reader),
    user=Depends(current_user),
) -> dict:
    """List industries at a level — feeds the neutralization dropdown."""
    sec = pl.from_arrow(reader.read_sectors(classification_system=system, level=level))
    return {
        "system": system,
        "level": level,
        "items": [
            {"code": r["code"], "name": r["name"]}
            for r in sec.select(["code", "name"]).to_dicts()
        ],
    }


# Register the specific /universe/symbols/search + /universe/industries routes.
# This must run AFTER both function definitions but provides the routes that
# would otherwise be shadowed by /universe/{universe_id}. We re-build the
# router's route list to put these specific routes first.
def _reorder_universe_routes() -> None:
    """Move /universe/symbols/search and /universe/industries before
    /universe/{universe_id} so the catch-all doesn't shadow them."""
    specific_paths = ("/universe/symbols/search", "/universe/industries")
    specific = [r for r in router.routes if r.path in specific_paths]
    catchall = [r for r in router.routes if r.path == "/universe/{universe_id}"]
    other = [r for r in router.routes if r not in specific and r not in catchall]
    # specific first, then catch-all (and its siblings), then other variants
    router.routes = specific + other + catchall + [
        r for r in router.routes
        if r.path.startswith("/universe/{universe_id}/")
    ]
    # Deduplicate while preserving order
    seen = set()
    out = []
    for r in router.routes:
        key = (r.path, tuple(sorted(r.methods)))
        if key not in seen:
            seen.add(key)
            out.append(r)
    router.routes = out


_register_specific_universe_routes()
_reorder_universe_routes()
