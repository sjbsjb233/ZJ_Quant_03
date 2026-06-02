"""Job query/cancel routes (the web proxy forwards /api/v1/data/jobs/* here).

Mirrors quant_web.jobs.routes; the progress drawer polls these through the web
proxy, so the wire shape is identical.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..db.models import Job
from ..deps import CallingUser, current_user, get_db
from ..errors import ApiError

router = APIRouter(prefix="/jobs", tags=["jobs"])


def job_to_dict(job: Job) -> dict:
    progress = None
    if job.progress_total:
        progress = {
            "current": job.progress_current,
            "total": job.progress_total,
            "unit": job.progress_unit,
            "estimated_remaining_sec": None,
        }
    return {
        "job_id": job.job_id,
        "type": job.type,
        "status": job.status,
        "progress": progress,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "log": job.log or [],
        "result": job.result,
        "error": job.error,
        "initiated_by": job.initiated_by,
        "trace_id": job.trace_id,
        "created_at": job.created_at,
    }


@router.get("")
def list_jobs(db: Session = Depends(get_db), user: CallingUser = Depends(current_user),
              limit: int = 20):
    q = (
        db.query(Job)
        .filter(Job.initiated_by == user.username)
        .order_by(Job.created_at.desc())
        .limit(min(limit, 100))
    )
    return {"items": [job_to_dict(j) for j in q.all()]}


@router.get("/recent")
def list_recent_jobs(db: Session = Depends(get_db), user: CallingUser = Depends(current_user),
                     limit: int = 50, type: str | None = None, status: str | None = None):
    """All recent jobs across initiators — the 可观测 任务台账 (§7.3⑦). Read-only
    metadata (type/status/times/trace), incl. autonomous scheduler runs that the
    per-initiator ``GET /jobs`` would hide."""
    q = db.query(Job)
    if type:
        q = q.filter(Job.type == type)
    if status:
        q = q.filter(Job.status.in_([s.strip() for s in status.split(",") if s.strip()]))
    rows = q.order_by(Job.created_at.desc()).limit(min(limit, 200)).all()
    return {"items": [job_to_dict(j) for j in rows]}


def _require_owned_job(job_id: str, db: Session, user: CallingUser) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise ApiError("JOB_NOT_FOUND", f"job {job_id} 不存在")
    if job.initiated_by != user.username and user.role != "admin":
        raise ApiError("PERMISSION_DENIED", "无权访问他人发起的任务")
    return job


@router.get("/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db),
            user: CallingUser = Depends(current_user)):
    return job_to_dict(_require_owned_job(job_id, db, user))


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, request: Request, db: Session = Depends(get_db),
               user: CallingUser = Depends(current_user)):
    _require_owned_job(job_id, db, user)
    ok = request.app.state.job_runner.cancel(job_id)
    return {"job_id": job_id, "cancel_requested": ok}
