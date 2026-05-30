"""Field-registry write commands (ported from quant_web.data.fields, writes only).

The promote/deprecate state machine + thresholds are preserved verbatim; the
``/migrate`` endpoint submits a heavy field_migration job to keeper's in-process
JobRunner and returns the job_id (the web proxy relays it; the progress drawer
polls /jobs through the proxy).
"""
from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends, Request
from quant_data import config as qd_config
from quant_data import migration
from quant_data.metadata import field_registry
from sqlalchemy.orm import Session

from ..db.models import Job
from ..deps import get_db, get_root, require_admin, require_editor
from ..errors import ApiError
from ..jobs.actors import field_migration as field_migration_actor
from .schemas import AddConsumerRequest, FieldUpdate, MigrateRequest, PromoteRequest

router = APIRouter(prefix="/fields", tags=["fields"])

# allowed state transitions; (from, to) -> needs_thresholds
_ALLOWED = {
    ("extras", "promoted"): True,
    ("promoted", "extras"): False,
    ("promoted", "deprecated"): False,
    ("core", "deprecated"): False,
    ("deprecated", "promoted"): False,
}
# next-state options per status (must match quant_web.data.fields._NEXT so the
# forwarded response carries the identical can_promote_to shape).
_NEXT = {
    "extras": ["promoted"],
    "promoted": ["core", "extras", "deprecated"],
    "core": ["deprecated"],
    "deprecated": ["promoted"],
}


def _age_days(f: dt.datetime | None) -> int | None:
    if f is None:
        return None
    return (dt.datetime.utcnow() - f).days


def _consumers(f: dict) -> list[str]:
    return json.loads(f["consumers"]) if f.get("consumers") else []


def _require_field(table: str, field: str, root: str) -> dict:
    f = field_registry.get_field(table, field, root=root)
    if f is None:
        raise ApiError("FIELD_NOT_FOUND", f"字段 {table}.{field} 不存在")
    return f


def _slim(f: dict) -> dict:
    """Full field echo — IDENTICAL shape to quant_web.data.fields._to_dict so a
    forwarded write response matches the old monolith byte-for-byte (体感不变)."""
    age = _age_days(f.get("first_seen_at"))
    consumers = _consumers(f)
    blockers = []
    if f["status"] == "extras":
        if age is None or age < qd_config.PROMOTE_MIN_AGE_DAYS:
            blockers.append(f"存在时长需 ≥ {qd_config.PROMOTE_MIN_AGE_DAYS} 天 (当前 {age})")
        if len(consumers) < 1:
            blockers.append("至少需要 1 个消费者")
    return {
        "table_name": f["table_name"],
        "field_name": f["field_name"],
        "data_type": f["data_type"],
        "status": f["status"],
        "description": f.get("description") or "",
        "unit": f.get("unit"),
        "first_seen_batch_id": f.get("first_seen_batch_id"),
        "first_seen_at": f.get("first_seen_at"),
        "promoted_at": f.get("promoted_at"),
        "core_at": f.get("core_at"),
        "deprecated_at": f.get("deprecated_at"),
        "authoritative_source": f.get("authoritative_source"),
        "consumers": consumers,
        "age_days": age,
        "can_promote_to": _NEXT.get(f["status"], []),
        "promote_blockers": blockers,
    }


@router.patch("/{table}/{field}")
def update_field(table: str, field: str, body: FieldUpdate, root: str = Depends(get_root),
                 user=Depends(require_editor)):
    _require_field(table, field, root)
    extra = {}
    if body.description is not None:
        extra["description"] = body.description
    if body.unit is not None:
        extra["unit"] = body.unit
    if body.authoritative_source is not None:
        extra["authoritative_source"] = body.authoritative_source
    if body.consumers is not None:
        extra["consumers"] = json.dumps(body.consumers)
    if not extra:
        raise ApiError("INVALID_PARAMETER", "没有要更新的字段")
    cur = field_registry.get_field(table, field, root=root)["status"]
    row = field_registry.update_field_status(table, field, cur, root=root, **extra)
    return _slim(row)


@router.post("/{table}/{field}/promote")
def promote(table: str, field: str, body: PromoteRequest, root: str = Depends(get_root),
            user=Depends(require_editor)):
    f = _require_field(table, field, root)
    cur, target = f["status"], body.status
    if target == "core":
        raise ApiError("INVALID_STATE_TRANSITION", "升级到 core 需走 /migrate 迁移流程")
    if (cur, target) not in _ALLOWED:
        raise ApiError("INVALID_STATE_TRANSITION", f"不允许 {cur} -> {target}",
                       {"current_state": cur, "target": target})
    if _ALLOWED[(cur, target)]:  # extras -> promoted threshold check
        age = _age_days(f.get("first_seen_at"))
        consumers = body.consumers if body.consumers is not None else _consumers(f)
        if age is None or age < qd_config.PROMOTE_MIN_AGE_DAYS:
            raise ApiError("INVALID_PARAMETER",
                           f"字段存在时长需 ≥ {qd_config.PROMOTE_MIN_AGE_DAYS} 天")
        if len(consumers) < 1:
            raise ApiError("INVALID_PARAMETER", "升级到 promoted 至少需要 1 个消费者")
    row = field_registry.promote_field(
        table, field, status=target, consumers=body.consumers,
        authoritative_source=body.authoritative_source, root=root,
    )
    return _slim(row)


@router.post("/{table}/{field}/deprecate")
def deprecate(table: str, field: str, root: str = Depends(get_root),
              user=Depends(require_editor)):
    f = _require_field(table, field, root)
    if f["status"] not in ("core", "promoted"):
        raise ApiError("INVALID_STATE_TRANSITION", "只有 core/promoted 字段可弃用",
                       {"current_state": f["status"]})
    row = field_registry.promote_field(table, field, status="deprecated", root=root)
    return _slim(row)


@router.post("/{table}/{field}/migrate")
def migrate(table: str, field: str, body: MigrateRequest, request: Request,
            root: str = Depends(get_root), db: Session = Depends(get_db),
            user=Depends(require_admin)):
    f = _require_field(table, field, root)
    if body.dry_run:
        return migration.promote_field_to_core(table, field, dtype=body.dtype,
                                                dry_run=True, root=root)
    if f["status"] != "promoted":
        raise ApiError("INVALID_STATE_TRANSITION", "只有 promoted 字段可迁移到 core",
                       {"current_state": f["status"]})
    running = db.query(Job).filter(
        Job.type == "field_migration", Job.status.in_(["pending", "running"])
    ).all()
    for j in running:
        if (j.params or {}).get("table") == table and (j.params or {}).get("field") == field:
            raise ApiError("MIGRATION_IN_PROGRESS", f"{table}.{field} 已有迁移在进行")
    job_id = request.app.state.job_runner.submit(
        "field_migration", field_migration_actor, initiated_by=user.username,
        params={"table": table, "field": field, "dtype": body.dtype},
    )
    return {"job_id": job_id}


@router.post("/{table}/{field}/consumers")
def add_consumer(table: str, field: str, body: AddConsumerRequest,
                 root: str = Depends(get_root), user=Depends(require_editor)):
    _require_field(table, field, root)
    consumer = (body.consumer or "").strip()
    if not consumer:
        raise ApiError("INVALID_PARAMETER", "consumer 必填")
    row = field_registry.add_consumer(table, field, consumer, root=root)
    return _slim(row)
