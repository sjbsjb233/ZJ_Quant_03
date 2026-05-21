"""Maintenance routes (doc §4.8 / §5.2.6) -- vacuum / compact / snapshot (admin)."""
from __future__ import annotations

import datetime as dt
import io
import os
import re
import shutil
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from quant_data import compaction, config as qd_config, table_stats
from quant_data.schemas import RAW_TABLES

from ..audit.log import Auditor
from ..deps import get_auditor, get_current_user, get_root, require_admin
from ..errors import ApiError
from ..jobs.actors import compact as compact_actor
from ..jobs.actors import snapshot as snapshot_actor
from ..jobs.actors import vacuum as vacuum_actor
from .schemas import (
    CompactExecuteRequest,
    CompactPreviewRequest,
    VacuumExecuteRequest,
    VacuumPreviewRequest,
)

router = APIRouter(prefix="/maintenance", tags=["maintenance"])


def _tables(requested: list[str] | None) -> list[str]:
    if not requested:
        return list(RAW_TABLES)
    bad = [t for t in requested if t not in RAW_TABLES]
    if bad:
        raise ApiError("INVALID_PARAMETER", f"不支持维护的表: {bad}")
    return requested


@router.post("/vacuum/preview")
def vacuum_preview(body: VacuumPreviewRequest, root: str = Depends(get_root),
                   user=Depends(require_admin)):
    per_table, files_all, total_bytes = [], [], 0
    for t in _tables(body.tables):
        plan = compaction.dead_files(t, older_than_days=body.older_than_days, root=root)
        uniq = {}
        for p in plan:
            f = p["file"]
            if f not in uniq and os.path.exists(f):
                uniq[f] = os.path.getsize(f)
                files_all.append({"path": f, "batch_id": p["batch_id"],
                                  "size": uniq[f], "invalidated_at": p.get("invalidated_at")})
        size = sum(uniq.values())
        total_bytes += size
        if uniq:
            per_table.append({"table": t, "files": len(uniq), "bytes": size})
    return {"file_count": len(files_all), "total_bytes": total_bytes,
            "by_table": per_table, "files": files_all[:200]}


@router.post("/vacuum/execute")
def vacuum_execute(body: VacuumExecuteRequest, request: Request,
                   user=Depends(require_admin), auditor: Auditor = Depends(get_auditor)):
    if body.confirm != "VACUUM":
        raise ApiError("INVALID_PARAMETER", '确认短语必须为 "VACUUM"')
    tables = _tables(body.tables)
    job_id = request.app.state.job_runner.submit(
        "vacuum", vacuum_actor, initiated_by=user.username,
        params={"tables": tables, "older_than_days": body.older_than_days},
    )
    auditor.record("maintenance.vacuum", "maintenance", job_id,
                   payload={"tables": tables, "older_than_days": body.older_than_days},
                   event="maintenance.vacuum_executed")
    return {"job_id": job_id}


@router.post("/compact/preview")
def compact_preview(body: CompactPreviewRequest, root: str = Depends(get_root),
                    user=Depends(require_admin)):
    out, total = [], 0
    for t in _tables(body.tables):
        res = compaction.compact(t, confirm=False, root=root)
        wc = res.get("would_compact", {})
        partitions = [{"partition": Path(d).name, "files": n} for d, n in wc.items()]
        total += len(partitions)
        if partitions:
            out.append({"table": t, "partitions": partitions})
    return {"by_table": out, "partition_count": total}


@router.post("/compact/execute")
def compact_execute(body: CompactExecuteRequest, request: Request,
                    user=Depends(require_admin), auditor: Auditor = Depends(get_auditor)):
    if body.confirm != "COMPACT":
        raise ApiError("INVALID_PARAMETER", '确认短语必须为 "COMPACT"')
    tables = _tables(body.tables)
    job_id = request.app.state.job_runner.submit(
        "compact", compact_actor, initiated_by=user.username, params={"tables": tables},
    )
    auditor.record("maintenance.compact", "maintenance", job_id, payload={"tables": tables},
                   event="maintenance.compact_executed")
    return {"job_id": job_id}


def _snap_dir(root: str) -> Path:
    return qd_config.data_root(root) / "metadata" / "_snapshots"


@router.post("/snapshot")
def snapshot(root: str = Depends(get_root), user=Depends(require_admin),
             auditor: Auditor = Depends(get_auditor)):
    path = compaction.snapshot_metadata(root=root)
    p = Path(path)
    size = sum(f.stat().st_size for f in p.glob("*") if f.is_file())
    auditor.record("maintenance.snapshot", "snapshot", p.name, payload={"path": path})
    return {"snapshot_id": p.name, "path": path, "size": size,
            "files": [f.name for f in p.glob("*") if f.is_file()]}


@router.get("/snapshots")
def list_snapshots(root: str = Depends(get_root), user=Depends(get_current_user)):
    base = _snap_dir(root)
    out = []
    if base.exists():
        for d in sorted(base.iterdir(), reverse=True):
            if d.is_dir():
                files = [f for f in d.glob("*") if f.is_file()]
                out.append({"snapshot_id": d.name,
                            "size": sum(f.stat().st_size for f in files),
                            "file_count": len(files),
                            "created_at": dt.datetime.utcfromtimestamp(d.stat().st_mtime)})
    return {"items": out, "total": len(out)}


# snapshot ids are timestamp dir names (e.g. 20260521T093218); reject anything else
_SNAPSHOT_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _resolve_snapshot(root: str, snapshot_id: str) -> Path:
    """Resolve a snapshot dir, refusing path traversal / absolute / separator tricks."""
    base = _snap_dir(root).resolve()
    if not _SNAPSHOT_ID_RE.match(snapshot_id):
        raise ApiError("SNAPSHOT_NOT_FOUND", f"快照 {snapshot_id} 不存在")
    target = (base / snapshot_id).resolve()
    # must stay strictly under the snapshots base dir and actually exist
    if base != target.parent or not target.is_dir():
        raise ApiError("SNAPSHOT_NOT_FOUND", f"快照 {snapshot_id} 不存在")
    return target


@router.delete("/snapshots/{snapshot_id}")
def delete_snapshot(snapshot_id: str, root: str = Depends(get_root),
                    user=Depends(require_admin), auditor: Auditor = Depends(get_auditor)):
    target = _resolve_snapshot(root, snapshot_id)
    shutil.rmtree(target)
    auditor.record("maintenance.snapshot_delete", "snapshot", snapshot_id)
    return {"snapshot_id": snapshot_id, "deleted": True}


@router.get("/snapshots/{snapshot_id}/download")
def download_snapshot(snapshot_id: str, root: str = Depends(get_root),
                      user=Depends(get_current_user)):
    target = _resolve_snapshot(root, snapshot_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in target.glob("*"):
            if f.is_file():
                z.write(f, f.name)
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="snapshot-{snapshot_id}.zip"'},
    )


@router.post("/refresh-stats")
def refresh_stats(root: str = Depends(get_root), user=Depends(require_admin)):
    rows = table_stats.refresh_all(root=root)
    return {"refreshed": len(rows)}
