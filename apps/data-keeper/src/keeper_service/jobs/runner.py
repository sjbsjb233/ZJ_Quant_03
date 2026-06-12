"""In-process job runner: thread pool + keeper.db Job table (V3 §4.2 / §4.2.1).

Ported from quant_web.jobs.runner. Differences:
  - logs go through ``quant_data.logging`` (shipped as source="data");
  - the job's ``trace_id`` is stored at submit() and re-bound via
    ``qd_logging.bind_context`` for the duration of the worker thread, so every
    quant_data write/QC log emitted by the actor carries the originating trace_id
    (V3 §6.4) — whether the trace came from a forwarded UI command or was minted
    by the scheduler for an autonomous run.
  - 内存治理 WP5 接入点②: heavy job types pass the quant_admission budget gate
    before executing (Deferred ⇒ the worker thread waits, heartbeating, until
    the budget frees up — 决定何时开始,不碰怎么算).
"""
from __future__ import annotations

import datetime as dt
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from quant_admission import AdmissionGate, Grant
from quant_data import logging as qd_logging
from sqlalchemy.orm import sessionmaker

from ..db.models import Job
from ..logging_ctx import new_trace_id, trace_id_var

_log = qd_logging.get_logger("keeper.jobs")

# job type → 准入预算类 (内存治理 WP5; 预算表见 ADMISSION_BUDGETS_JSON)。
# 重型写 (compaction/migration/vacuum 走 maintenance 类) 按 keeper.compact 记账;
# 其余按 keeper.job.light 小预算 (排干期一并暂停)。
_ADMISSION_CLASS = {
    "maintenance": "keeper.compact",
    "compaction": "keeper.compact",
    "migration": "keeper.compact",
    "vacuum": "keeper.compact",
}
_ADMISSION_POLL_S = 2.0

_gate: AdmissionGate | None = None
_gate_lock = threading.Lock()


def _admission_gate() -> AdmissionGate:
    global _gate
    if _gate is None:
        with _gate_lock:
            if _gate is None:
                _gate = AdmissionGate.from_env()
    return _gate


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
        # progress_at = heartbeat for the回收线程 (D6): a job updating progress is
        # alive, not 卡死, even if it's over its time budget.
        self.runner._update(self.job_id, progress_current=current, progress_total=total,
                            progress_unit=unit, progress_at=dt.datetime.utcnow())

    def log(self, message: str) -> None:
        ts = dt.datetime.utcnow().strftime("%H:%M:%S")
        self.runner._append_log(self.job_id, f"[{ts}] {message}")

    def check_cancel(self) -> None:
        if self.runner._is_cancelled(self.job_id):
            raise JobCancelled(f"job {self.job_id} cancelled")


class JobRunner:
    def __init__(self, session_factory: sessionmaker, root: str, workers: int = 4):
        self._sf = session_factory
        self._root = root
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")
        self._cancel: set[str] = set()
        self._cancel_lock = threading.Lock()

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
               params: dict | None = None, trace_id: str | None = None) -> str:
        job_id = f"job_{uuid.uuid4().hex[:16]}"
        # capture the active request/scheduler trace_id so the worker can re-bind.
        tid = trace_id or trace_id_var.get() or new_trace_id()
        with self._sf() as db:
            db.add(Job(job_id=job_id, type=job_type, status="pending",
                       initiated_by=initiated_by, params=params or {}, log=[],
                       progress_unit="items", trace_id=tid))
            db.commit()
        _log.info(f"job.{job_type}.submit", job_id=job_id, job_type=job_type,
                  initiated_by=initiated_by, params=params or {})
        self._pool.submit(self._run, job_id, job_type, fn, params or {}, tid)
        return job_id

    def cancel(self, job_id: str) -> bool:
        with self._sf() as db:
            job = db.get(Job, job_id)
            if job is None or job.status not in ("pending", "running"):
                return False
        self._mark_cancel(job_id)
        return True

    # -- internals -----------------------------------------------------------
    def _run(self, job_id: str, job_type: str, fn: Callable, params: dict,
             trace_id: str | None) -> None:
        # re-bind the originating trace_id for the whole worker-thread execution
        # so quant_data's write/QC logs join the same trace (V3 §6.4).
        local_tok = trace_id_var.set(trace_id)
        qd_tok = qd_logging.bind_context(trace_id=trace_id)
        _now = dt.datetime.utcnow()
        self._update(job_id, status="running", started_at=_now, progress_at=_now)
        _log.info(f"job.{job_type}.start", job_id=job_id, job_type=job_type)
        started = dt.datetime.utcnow()
        ctx = JobContext(self, job_id, self._root)
        grant: Grant | None = None
        try:
            grant = self._acquire_admission(ctx, job_id, job_type)
            result = fn(ctx, **params)
            if self._is_cancelled(job_id):
                raise JobCancelled(job_id)
            self._update(job_id, status="success", result=result or {},
                         finished_at=dt.datetime.utcnow())
            _log.info(f"job.{job_type}.done", job_id=job_id, job_type=job_type,
                      duration_ms=_ms_since(started))
        except JobCancelled:
            self._update(job_id, status="cancelled", finished_at=dt.datetime.utcnow())
            _log.warn(f"job.{job_type}.cancelled", job_id=job_id, job_type=job_type,
                      duration_ms=_ms_since(started))
        except Exception as exc:  # noqa: BLE001
            self._update(job_id, status="failed", error=str(exc),
                         finished_at=dt.datetime.utcnow())
            _log.error(f"job.{job_type}.failed", exc=exc, job_id=job_id,
                       job_type=job_type, duration_ms=_ms_since(started))
        finally:
            if grant is not None:
                grant.release()
            self._clear_cancel(job_id)
            qd_logging.reset_context(qd_tok)
            trace_id_var.reset(local_tok)

    def _acquire_admission(self, ctx: "JobContext", job_id: str, job_type: str) -> Grant:
        """内存治理 WP5 接入点②: budget gate before the actor runs.

        Deferred ⇒ wait-and-retry (cancellable). Each retry bumps progress_at
        (heartbeat) so the reaper's stale-heartbeat回收 (D6) never mistakes a
        budget-queued job for a hung one; the wait itself is visible in the
        job log once + the gate's own admission.* app_log chain.
        """
        cls = _ADMISSION_CLASS.get(job_type, "keeper.job.light")
        gate = _admission_gate()
        logged_wait = False
        while True:
            ctx.check_cancel()
            verdict = gate.try_acquire(cls, task_id=job_id)
            if isinstance(verdict, Grant):
                return verdict
            if not logged_wait:
                logged_wait = True
                ctx.log(f"等待内存准入 ({cls}): {verdict.reason}")
            self._update(job_id, progress_at=dt.datetime.utcnow())  # 心跳防误回收
            time.sleep(min(_ADMISSION_POLL_S, max(0.5, verdict.suggested_wait_s)))

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
            job.progress_at = dt.datetime.utcnow()   # heartbeat (D6)
            db.commit()
