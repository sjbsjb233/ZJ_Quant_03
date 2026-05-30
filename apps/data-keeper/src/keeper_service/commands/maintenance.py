"""Maintenance write commands (ported from quant_web.data.maintenance, writes only).

Heavy writes (vacuum/compact execute) submit a job to keeper's JobRunner; the
lightweight ones (snapshot create/delete, refresh-stats) run synchronously. The
read-only previews stay in web (they don't need :rw). (V3 §4.1)
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from quant_data import compaction, config as qd_config, table_stats
from quant_data.schemas import RAW_TABLES

from ..deps import get_root, require_admin
from ..errors import ApiError
from ..jobs.actors import compact as compact_actor
from ..jobs.actors import vacuum as vacuum_actor
from .schemas import CompactExecuteRequest, VacuumExecuteRequest

router = APIRouter(prefix="/maintenance", tags=["maintenance"])


def _tables(requested: list[str] | None) -> list[str]:
    if not requested:
        return list(RAW_TABLES)
    bad = [t for t in requested if t not in RAW_TABLES]
    if bad:
        raise ApiError("INVALID_PARAMETER", f"不支持维护的表: {bad}")
    return requested


@router.post("/vacuum/execute")
def vacuum_execute(body: VacuumExecuteRequest, request: Request, user=Depends(require_admin)):
    if body.confirm != "VACUUM":
        raise ApiError("INVALID_PARAMETER", '确认短语必须为 "VACUUM"')
    tables = _tables(body.tables)
    job_id = request.app.state.job_runner.submit(
        "vacuum", vacuum_actor, initiated_by=user.username,
        params={"tables": tables, "older_than_days": body.older_than_days},
    )
    return {"job_id": job_id}


@router.post("/compact/execute")
def compact_execute(body: CompactExecuteRequest, request: Request, user=Depends(require_admin)):
    if body.confirm != "COMPACT":
        raise ApiError("INVALID_PARAMETER", '确认短语必须为 "COMPACT"')
    tables = _tables(body.tables)
    job_id = request.app.state.job_runner.submit(
        "compact", compact_actor, initiated_by=user.username, params={"tables": tables},
    )
    return {"job_id": job_id}


def _snap_dir(root: str) -> Path:
    return qd_config.data_root(root) / "metadata" / "_snapshots"


@router.post("/snapshot")
def snapshot(root: str = Depends(get_root), user=Depends(require_admin)):
    path = compaction.snapshot_metadata(root=root)
    p = Path(path)
    size = sum(f.stat().st_size for f in p.glob("*") if f.is_file())
    return {"snapshot_id": p.name, "path": path, "size": size,
            "files": [f.name for f in p.glob("*") if f.is_file()]}


# snapshot ids are timestamp dir names (e.g. 20260521T093218); reject anything else
_SNAPSHOT_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _resolve_snapshot(root: str, snapshot_id: str) -> Path:
    base = _snap_dir(root).resolve()
    if not _SNAPSHOT_ID_RE.match(snapshot_id):
        raise ApiError("SNAPSHOT_NOT_FOUND", f"快照 {snapshot_id} 不存在")
    target = (base / snapshot_id).resolve()
    if base != target.parent or not target.is_dir():
        raise ApiError("SNAPSHOT_NOT_FOUND", f"快照 {snapshot_id} 不存在")
    return target


@router.delete("/snapshots/{snapshot_id}")
def delete_snapshot(snapshot_id: str, root: str = Depends(get_root), user=Depends(require_admin)):
    target = _resolve_snapshot(root, snapshot_id)
    shutil.rmtree(target)
    return {"snapshot_id": snapshot_id, "deleted": True}


@router.post("/refresh-stats")
def refresh_stats(root: str = Depends(get_root), user=Depends(require_admin)):
    rows = table_stats.refresh_all(root=root)
    return {"refreshed": len(rows)}
