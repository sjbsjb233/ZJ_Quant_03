"""Job query/cancel routes (doc §5.2.7)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..db.base import get_db
from ..db.models import Job, User
from ..deps import get_current_user
from ..errors import ApiError

router = APIRouter(prefix="/jobs", tags=["jobs"])


def job_to_dict(job: Job) -> dict:
    progress = None
    if job.progress_total:
        remaining = None
        progress = {
            "current": job.progress_current,
            "total": job.progress_total,
            "unit": job.progress_unit,
            "estimated_remaining_sec": remaining,
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
        "created_at": job.created_at,
    }


@router.get("")
def list_jobs(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    limit: int = 20,
):
    q = (
        db.query(Job)
        .filter(Job.initiated_by == user.username)
        .order_by(Job.created_at.desc())
        .limit(min(limit, 100))
    )
    return {"items": [job_to_dict(j) for j in q.all()]}


def _require_owned_job(job_id: str, db: Session, user: User) -> Job:
    """Fetch a job, enforcing that only its initiator (or an admin) can see/act on it.

    Avoids leaking another user's job logs/results (which may contain file paths).
    """
    job = db.get(Job, job_id)
    if job is None:
        raise ApiError("JOB_NOT_FOUND", f"job {job_id} 不存在")
    if job.initiated_by != user.username and user.role != "admin":
        raise ApiError("PERMISSION_DENIED", "无权访问他人发起的任务")
    return job


@router.get("/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db),
            user: User = Depends(get_current_user)):
    return job_to_dict(_require_owned_job(job_id, db, user))


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, request: Request, db: Session = Depends(get_db),
               user: User = Depends(get_current_user)):
    _require_owned_job(job_id, db, user)
    ok = request.app.state.job_runner.cancel(job_id)
    return {"job_id": job_id, "cancel_requested": ok}
