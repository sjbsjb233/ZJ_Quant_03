"""Stuck-job reaper (可观测层重构方案 §6.5 · D6).

A daemon thread that wakes every ``reaper_tick_sec`` and reclaims ``running`` jobs
that are **both** past their per-type time budget **and** showing no progress
heartbeat for ``job_heartbeat_stale_min`` —— i.e. genuinely 卡死, not merely slow.
Each reclaimed job is flipped to ``timeout`` and manufactures an ERROR so the
中枢 Alerter / 评估器 (which can't see a hung process) finally raises an alert.

Single writer ⇒ single instance ⇒ one reaper, no double-reclaim (mirrors the
in-process scheduler's reasoning).
"""
from __future__ import annotations

import datetime as dt
import threading

from quant_data import logging as qd_logging

from .config import Settings
from .db.models import Job

_log = qd_logging.get_logger("keeper.reaper")


def timeout_minutes_for(job_type: str, table: dict[str, int]) -> int:
    """Per-type budget: match a substring of the job type, else ``default``."""
    t = (job_type or "").lower()
    for key, minutes in table.items():
        if key != "default" and key in t:
            return minutes
    return table.get("default", 20)


class Reaper:
    def __init__(self, session_factory, settings: Settings):
        self._sf = session_factory
        self._s = settings
        self._budget = settings.job_timeout_by
        self._stale = settings.job_heartbeat_stale_min
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="keeper-reaper", daemon=True)

    def start(self) -> "Reaper":
        self._thread.start()
        _log.info("reaper.up", tick_seconds=self._s.reaper_tick_sec,
                  heartbeat_stale_min=self._stale, budget=self._budget)
        return self

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.reap_once()
            except Exception as exc:  # noqa: BLE001 - the loop must never die
                _log.error("reaper.tick.failed", exc=exc)
            self._stop.wait(self._s.reaper_tick_sec)

    def reap_once(self) -> int:
        """Reclaim every stuck running job. Returns how many were reaped."""
        now = dt.datetime.utcnow()
        reaped: list[tuple[str, str, str | None, int, int]] = []
        with self._sf() as db:
            running = db.query(Job).filter(Job.status == "running").all()
            for job in running:
                if job.started_at is None:
                    continue
                budget_min = timeout_minutes_for(job.type, self._budget)
                running_min = (now - job.started_at).total_seconds() / 60
                if running_min < budget_min:
                    continue                          # still within budget
                heartbeat = job.progress_at or job.started_at
                idle_min = (now - heartbeat).total_seconds() / 60
                if idle_min < self._stale:
                    continue                          # progressing → slow, not stuck
                job.status = "timeout"
                job.error = (f"timeout: running {int(running_min)}m > {budget_min}m budget, "
                             f"no progress for {int(idle_min)}m")
                job.finished_at = now
                reaped.append((job.job_id, job.type, job.trace_id,
                               int(running_min), int(idle_min)))
            if reaped:
                db.commit()
        for job_id, job_type, trace_id, running_min, idle_min in reaped:
            _log.error(f"job.{job_type}.timeout", job_id=job_id, job_type=job_type,
                       trace_id=trace_id, running_min=running_min, idle_min=idle_min,
                       message=f"job {job_id} ({job_type}) reclaimed: stuck {idle_min}m "
                               f"with no progress, over its {timeout_minutes_for(job_type, self._budget)}m budget")
        return len(reaped)


_reaper: Reaper | None = None


def start_reaper(session_factory, settings: Settings) -> Reaper:
    global _reaper
    _reaper = Reaper(session_factory, settings).start()
    return _reaper


def stop_reaper() -> None:
    if _reaper is not None:
        _reaper.stop()
