"""In-process scheduler thread (V3 §4.2 / §4.2.1).

A single daemon thread wakes every ``sched_tick_seconds`` and, when the local
wall-clock passes a task's daily trigger time (and it hasn't fired yet today),
enqueues that task into the JobRunner with a freshly-minted trace_id. Because
data-keeper is the single writer ⇒ single instance, this in-process cron can
never double-fire (no need for a separate scheduler container).

Each autonomous run gets its own trace_id (e.g. ``t_sched_crawl_20260529``) so
"采集 → 入库 → 质检 → 批次登记" is one chain in ``/logs/trace/{id}`` (V3 §6.4).
Tasks are idempotent (append-only + batch dedup), so a missed tick (container
restart) self-heals on the next pass.
"""
from __future__ import annotations

import datetime as dt
import threading

from quant_data import logging as qd_logging

from .config import Settings
from .jobs import actors

_log = qd_logging.get_logger("keeper.scheduler")


class Scheduler:
    def __init__(self, job_runner, settings: Settings):
        self._runner = job_runner
        self._s = settings
        self._stop = threading.Event()
        self._fired: dict[str, str] = {}   # task name -> ISO date last fired
        self._thread = threading.Thread(target=self._run, name="keeper-scheduler", daemon=True)
        # (task_name, "HH:MM", job_type, actor, params)
        self._tasks = [
            ("crawl", settings.sched_crawl_at, "crawl", actors.crawl, {"table": "raw.bars_daily"}),
            ("quality", settings.sched_quality_at, "quality_scan", actors.quality_scan, {}),
            ("maintenance", settings.sched_maintenance_at, "scheduled_maintenance",
             actors.scheduled_maintenance, {}),
        ]

    def start(self) -> "Scheduler":
        self._thread.start()
        _log.info("scheduler.up", tick_seconds=self._s.sched_tick_seconds,
                  crawl_at=self._s.sched_crawl_at, quality_at=self._s.sched_quality_at,
                  maintenance_at=self._s.sched_maintenance_at)
        return self

    def stop(self) -> None:
        self._stop.set()

    @staticmethod
    def _parse_hhmm(value: str) -> tuple[int, int]:
        h, _, m = (value or "").partition(":")
        return int(h or 0), int(m or 0)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001 - the loop must never die
                _log.error("scheduler.tick.failed", exc=exc)
            self._stop.wait(self._s.sched_tick_seconds)

    def _tick(self) -> None:
        now = dt.datetime.now()           # local time (TZ set via env in compose)
        today = now.date().isoformat()
        for name, at, job_type, actor, params in self._tasks:
            hh, mm = self._parse_hhmm(at)
            trigger = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if now >= trigger and self._fired.get(name) != today:
                self._fired[name] = today
                trace_id = f"t_sched_{name}_{today.replace('-', '')}"
                self._runner.submit(job_type, actor, initiated_by="scheduler",
                                    params=dict(params), trace_id=trace_id)
                _log.info("scheduler.enqueued", task=name, job_type=job_type, trace_id=trace_id)


_scheduler: Scheduler | None = None


def start_scheduler(job_runner, settings: Settings) -> Scheduler:
    global _scheduler
    _scheduler = Scheduler(job_runner, settings).start()
    return _scheduler


def stop_scheduler() -> None:
    if _scheduler is not None:
        _scheduler.stop()
