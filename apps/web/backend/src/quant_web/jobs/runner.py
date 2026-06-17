"""In-process job runner backed by a thread pool + the Job table (doc §7.3)."""
from __future__ import annotations

import datetime as dt
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy.orm import sessionmaker

from ..db.models import Job
from ..logging import app_logger


class JobCancelled(Exception):
    pass


def _ms_since(start: dt.datetime) -> int:
    return int((dt.datetime.utcnow() - start).total_seconds() * 1000)


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
        if self.runner._is_cancelled(self.job_id):
            raise JobCancelled(f"job {self.job_id} cancelled")


_TERMINAL_STATUSES = ("success", "failed", "cancelled")


class JobRunner:
    def __init__(self, session_factory: sessionmaker, root: str, workers: int = 4):
        self._sf = session_factory
        self._root = root
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")
        # shared between request threads (cancel) and worker threads (check/_run);
        # guard every access with this lock to avoid races.
        self._cancel: set[str] = set()
        self._cancel_lock = threading.Lock()

    def stop(self) -> None:
        """优雅关闭线程池: 取消未启动的任务并等待在途任务收尾(应用 shutdown 钩子调用)。"""
        self._pool.shutdown(wait=True, cancel_futures=True)

    def _is_cancelled(self, job_id: str) -> bool:
        with self._cancel_lock:
            return job_id in self._cancel

    def _mark_cancel(self, job_id: str) -> None:
        with self._cancel_lock:
            self._cancel.add(job_id)

    def _clear_cancel(self, job_id: str) -> None:
        with self._cancel_lock:
            self._cancel.discard(job_id)

    # -- public API ----------------------------------------------------------
    def submit(self, job_type: str, fn: Callable, initiated_by: str,
               params: dict | None = None) -> str:
        job_id = f"job_{uuid.uuid4().hex[:16]}"
        with self._sf() as db:
            db.add(Job(job_id=job_id, type=job_type, status="pending",
                       initiated_by=initiated_by, params=params or {}, log=[],
                       progress_unit="items"))
            db.commit()
        app_logger.info(f"job.{job_type}.submit", job_id=job_id, job_type=job_type,
                        initiated_by=initiated_by, params=params or {})
        self._pool.submit(self._run, job_id, job_type, fn, params or {})
        return job_id

    def cancel(self, job_id: str) -> bool:
        with self._sf() as db:
            job = db.get(Job, job_id)
            if job is None or job.status not in ("pending", "running"):
                return False
        self._mark_cancel(job_id)
        # TOCTOU 防护: worker 可能在上面的读取之后、_mark_cancel 之前就跑完并执行了
        # finally 里的 _clear_cancel, 导致这里新加入的 id 永远残留。复查一次状态,
        # 若已终态则立即清除, 避免 _cancel 集合无界增长。
        with self._sf() as db:
            job = db.get(Job, job_id)
            if job is None or job.status in _TERMINAL_STATUSES:
                self._clear_cancel(job_id)
        return True

    # -- internals -----------------------------------------------------------
    def _run(self, job_id: str, job_type: str, fn: Callable, params: dict) -> None:
        self._update(job_id, status="running", started_at=dt.datetime.utcnow())
        app_logger.info(f"job.{job_type}.start", job_id=job_id, job_type=job_type)
        started = dt.datetime.utcnow()
        ctx = JobContext(self, job_id, self._root)
        try:
            result = fn(ctx, **params)
            if self._is_cancelled(job_id):
                raise JobCancelled(job_id)
            self._update(job_id, status="success", result=result or {},
                         finished_at=dt.datetime.utcnow())
            app_logger.info(f"job.{job_type}.done", job_id=job_id, job_type=job_type,
                            duration_ms=_ms_since(started))
        except JobCancelled:
            self._update(job_id, status="cancelled", finished_at=dt.datetime.utcnow())
            app_logger.warn(f"job.{job_type}.cancelled", job_id=job_id, job_type=job_type,
                            duration_ms=_ms_since(started))
        except Exception as exc:  # noqa: BLE001
            self._update(job_id, status="failed", error=str(exc),
                         finished_at=dt.datetime.utcnow())
            app_logger.error(f"job.{job_type}.failed", exc=exc, job_id=job_id,
                             job_type=job_type, duration_ms=_ms_since(started))
        finally:
            self._clear_cancel(job_id)

    def _update(self, job_id: str, **fields) -> None:
        with self._sf() as db:
            job = db.get(Job, job_id)
            if job is None:
                return
            for k, v in fields.items():
                setattr(job, k, v)
            db.commit()
        # 任意路径将任务推入终态时都顺手清除取消标记, 与 cancel() 的复查互为兜底,
        # 防止 _cancel 集合残留已结束任务的 id。
        if fields.get("status") in _TERMINAL_STATUSES:
            self._clear_cancel(job_id)

    def _append_log(self, job_id: str, line: str) -> None:
        with self._sf() as db:
            job = db.get(Job, job_id)
            if job is None:
                return
            job.log = (job.log or []) + [line]
            db.commit()
