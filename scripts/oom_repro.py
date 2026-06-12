"""Controlled reproduction of the factor-worker OOM crash-loop.

Drives the REAL dispatcher loop + claim gate (factor_service.queue.dispatcher /
store) but replaces each task body with a deterministic RSS allocation, so the
only variable is FACTOR_HEAVY_CONCURRENCY. A real evaluation peaks at multiple
GB (measured 5.37GB whole-market on prod); here each "heavy" task allocates
ALLOC_MB and holds it, mirroring that footprint at small scale under a tight
container --memory limit.

  OLD behaviour  (FACTOR_HEAVY_CONCURRENCY=4): 4 tasks alloc concurrently →
                 exceed the mem cap → kernel OOM-kills the worker (exit 137,
                 OOMKilled=true) — the crash.
  FIXED          (FACTOR_HEAVY_CONCURRENCY=1 or 2): the gate caps concurrent
                 heavy tasks → peak RSS bounded → all tasks complete (exit 0).
"""
from __future__ import annotations

import logging
import os
import threading
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
_log = logging.getLogger("oom_repro")

ALLOC_MB = int(os.environ.get("ALLOC_MB", "420"))
HOLD_S = float(os.environ.get("HOLD_S", "5"))
N = int(os.environ.get("N_TASKS", "6"))

from factor_service.config import reset_settings_cache  # noqa: E402
reset_settings_cache()
from factor_service.db import base as db_base  # noqa: E402
db_base.rebind(os.environ["FACTOR_DB_URL"])
db_base.init_db()

from factor_service.queue import dispatcher, store  # noqa: E402
from factor_service.db.models import Task  # noqa: E402


def _heavy_stub(task_id: str) -> dict:
    """Allocate ALLOC_MB of *resident* memory and hold it for HOLD_S — mirrors an
    evaluation's RSS peak so concurrent ones contend for the container mem cap."""
    _log.info("task %s allocating %dMB", task_id, ALLOC_MB)
    buf = bytearray(ALLOC_MB * 1024 * 1024)
    # Touch every page so the pages are actually resident (real RSS, not lazy).
    for i in range(0, len(buf), 4096):
        buf[i] = 1
    t0 = time.time()
    while time.time() - t0 < HOLD_S:
        time.sleep(0.05)
    n = len(buf)
    del buf
    return {"task_id": task_id, "status": "success", "summary": {"alloc_mb": ALLOC_MB, "bytes": n}}


# Patch the name the dispatcher actually calls (imported at module load).
dispatcher.execute_task = _heavy_stub


def _seed_tasks() -> None:
    with db_base.SessionLocal() as db:
        live = db.query(Task).filter(Task.status.in_(("queued", "running"))).count()
        if live:
            _log.info("re-using %d non-terminal tasks (restart path)", live)
            return
        for i in range(N):
            store.enqueue(db, task_type="basket_eval", payload={"i": i})
        _log.info("enqueued %d heavy basket_eval tasks", N)


def _watch_and_stop() -> None:
    """Stop the dispatcher cleanly once all tasks reach a terminal state."""
    while True:
        time.sleep(0.5)
        with db_base.SessionLocal() as db:
            terminal = db.query(Task).filter(
                Task.status.in_(("success", "failed", "canceled"))
            ).count()
            total = db.query(Task).count()
        if total and terminal >= total:
            with db_base.SessionLocal() as db:
                rows = db.query(Task.status, Task.task_id).all()
            _log.info("ALL DONE — %d/%d terminal: %s", terminal, total,
                      {tid: st for st, tid in rows})
            dispatcher._stop.set()
            return


if __name__ == "__main__":
    _seed_tasks()
    threading.Thread(target=_watch_and_stop, daemon=True).start()
    _log.info("starting real dispatcher.main() — ALLOC_MB=%d HOLD_S=%.1f N=%d", ALLOC_MB, HOLD_S, N)
    dispatcher.main()
    _log.info("dispatcher.main() returned cleanly (all heavy tasks completed)")
