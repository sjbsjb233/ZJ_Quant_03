"""Startup job reconciliation (可观测层重构方案 §6.5 · D5).

When data-keeper is SIGKILL'd (OOM, ``docker kill``) the running JobRunner thread
dies without ever reaching its ``except`` —— so a crashed run leaves its ``Job``
row stuck at ``status=running`` forever, and not one ERROR is written. On the next
boot we **reconcile** those orphans: flip them to ``failed(interrupted)`` and emit
a real ERROR per orphan, so the中枢 Alerter / 评估器 finally sees that the process
died (the four-fold blind spot that hid the last P0).

``ensure_job_columns`` is a tiny forward-migration: keeper.db is created with
``create_all`` (no Alembic), so a new column (``progress_at``) must be ALTER'd onto
an existing file. Idempotent and best-effort.
"""
from __future__ import annotations

import datetime as dt

from quant_data import logging as qd_logging
from sqlalchemy import text

from .db.models import Job

_log = qd_logging.get_logger("keeper.reconcile")


def ensure_job_columns(engine) -> None:
    """Add columns introduced after the table's first creation (idempotent)."""
    wanted = {"progress_at": "DATETIME"}
    try:
        with engine.connect() as conn:
            existing = {row[1] for row in conn.execute(text("PRAGMA table_info(jobs)"))}
            for col, decl in wanted.items():
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE jobs ADD COLUMN {col} {decl}"))
            conn.commit()
    except Exception as exc:  # noqa: BLE001 - never block startup on a migration
        _log.warn("keeper.reconcile.migrate_failed", exc=exc)


def reconcile_orphans(session_factory) -> int:
    """Flip every leftover ``running`` Job → ``failed(interrupted)`` and ERROR-log
    each. Returns the number of orphans reconciled."""
    now = dt.datetime.utcnow()
    reconciled: list[Job] = []
    with session_factory() as db:
        orphans = db.query(Job).filter(Job.status == "running").all()
        for job in orphans:
            job.status = "failed"
            job.error = "interrupted (process restarted)"
            job.finished_at = now
            reconciled.append(job)
        if reconciled:
            db.commit()
        # snapshot the fields we need *before* the session closes
        info = [(j.job_id, j.type, j.trace_id) for j in reconciled]
    for job_id, job_type, trace_id in info:
        # actively manufacture an ERROR so the existing Alerter / 评估器 (D5) sees it.
        _log.error(f"job.{job_type}.interrupted", job_id=job_id, job_type=job_type,
                   trace_id=trace_id,
                   message=f"job {job_id} ({job_type}) was running when the process died; "
                           f"reconciled to failed(interrupted)")
    if info:
        _log.warn("keeper.reconcile.done", orphans=len(info))
    return len(info)
