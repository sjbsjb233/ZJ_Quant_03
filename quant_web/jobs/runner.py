"""In-process job runner backed by a thread pool + the Job table (doc §7.3)."""
from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import sessionmaker

from ..db.models import Job


class JobCancelled(Exception):
    pass


class JobContext:
    """Handed to an actor; reports progress/log + supports cooperative cancel."""

    def __init__(self, runner: "JobRunner", job_id: str, root: str):
        self.runner = runner
        self.job_id = job_id
        self.root = root

    def progress(self, current: int, total: int, unit: str = "items") -> None:
        self.check_cancel()
        self.runner._update(self.job_id, progress_current=current, progress_total=total,
                            progress_unit=unit)

    def log(self, message: str) -> None:
        ts = dt.datetime.utcnow().strftime("%H:%M:%S")
        self.runner._append_log(self.job_id, f"[{ts}] {message}")

    def check_cancel(self) -> None:
        if self.job_id in self.runner._cancel:
            raise JobCancelled(f"job {self.job_id} cancelled")


class JobRunner:
    def __init__(self, session_factory: sessionmaker, root: str, workers: int = 4):
        self._sf = session_factory
        self._root = root
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")
        self._cancel: set[str] = set()

    # -- public API ----------------------------------------------------------
    def submit(self, job_type: str, fn: Callable, initiated_by: str,
               params: dict | None = None) -> str:
        job_id = f"job_{uuid.uuid4().hex[:16]}"
        with self._sf() as db:
            db.add(Job(job_id=job_id, type=job_type, status="pending",
                       initiated_by=initiated_by, params=params or {}, log=[],
                       progress_unit="items"))
            db.commit()
        self._pool.submit(self._run, job_id, fn, params or {})
        return job_id

    def cancel(self, job_id: str) -> bool:
        with self._sf() as db:
            job = db.get(Job, job_id)
            if job is None or job.status not in ("pending", "running"):
                return False
        self._cancel.add(job_id)
        return True

    # -- internals -----------------------------------------------------------
    def _run(self, job_id: str, fn: Callable, params: dict) -> None:
        self._update(job_id, status="running", started_at=dt.datetime.utcnow())
        ctx = JobContext(self, job_id, self._root)
        try:
            result = fn(ctx, **params)
            if job_id in self._cancel:
                raise JobCancelled(job_id)
            self._update(job_id, status="success", result=result or {},
                         finished_at=dt.datetime.utcnow())
        except JobCancelled:
            self._update(job_id, status="cancelled", finished_at=dt.datetime.utcnow())
        except Exception as exc:  # noqa: BLE001
            self._update(job_id, status="failed", error=str(exc),
                         finished_at=dt.datetime.utcnow())
        finally:
            self._cancel.discard(job_id)

    def _update(self, job_id: str, **fields) -> None:
        with self._sf() as db:
            job = db.get(Job, job_id)
            if job is None:
                return
            for k, v in fields.items():
                setattr(job, k, v)
            db.commit()

    def _append_log(self, job_id: str, line: str) -> None:
        with self._sf() as db:
            job = db.get(Job, job_id)
            if job is None:
                return
            job.log = (job.log or []) + [line]
            db.commit()
