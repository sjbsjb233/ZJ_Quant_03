"""进程级资源采样器 (资源画像方案 v1.2 §3.1).

worker (dispatcher) 进程内单线程,随 dispatcher 启动,每 1s 采一次:

  - 进程 RSS (``/proc/self/statm``;不依赖 psutil — 容器里 psutil 读宿主口径且未装)
  - 进程 CPU% (``/proc/self/stat`` utime+stime 增量 / 墙钟,单核百分比可 >100)
  - ``pyarrow.total_allocated_bytes()`` (Arrow 分配账本,进程级)
  - 当前 in_flight 任务集合 + 各任务活跃阶段 (由任务线程写入 :class:`InFlightRegistry`)

写入 ① 内存环形缓冲 (容量 2h,供任务收尾切片) ② 当日 NDJSON
``runs_dir/worker_samples/{YYYYMMDD}.ndjson`` (供篮子/全局视图切片)。

失败安全红线 (方案 §3.1):采样器任何异常仅打 ``app_log`` 警告,**绝不**影响任务
执行;采样器死亡由 ``sampler_alive()`` 暴露给 worker 心跳 (降级标记,画像缺失但
任务照跑)。开销预算 1Hz < 0.5% 单核。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings, get_settings

_log = logging.getLogger("factor.resource_sampler")

_PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
try:
    _CLK_TCK = os.sysconf("SC_CLK_TCK")
except (ValueError, OSError, AttributeError):
    _CLK_TCK = 100


# ─────────────────────────────────────────────────────────────────────────────
# Low-level /proc readers — Linux only; every reader returns a safe default so a
# non-Linux dev box (or a sandboxed /proc) degrades the画像, never crashes a run.
def read_rss_mb() -> float:
    """Resident set size in MB from ``/proc/self/statm`` (field 2 = RSS pages)."""
    try:
        with open("/proc/self/statm") as f:
            pages = int(f.read().split()[1])
        return pages * _PAGE_SIZE / 1048576.0
    except (OSError, ValueError, IndexError):
        try:  # macOS dev fallback — ru_maxrss is KB on Linux, bytes on macOS
            import resource as _r
            return _r.getrusage(_r.RUSAGE_SELF).ru_maxrss / 1024.0
        except Exception:  # noqa: BLE001
            return 0.0


def read_proc_cpu_seconds() -> float:
    """Process CPU seconds (utime+stime) from ``/proc/self/stat``."""
    try:
        with open("/proc/self/stat") as f:
            data = f.read()
        # utime/stime are fields 14/15 (1-based) AFTER the comm field, which may
        # itself contain spaces/parens — split on the last ')' to be safe.
        rest = data[data.rfind(")") + 1:].split()
        utime, stime = int(rest[11]), int(rest[12])
        return (utime + stime) / _CLK_TCK
    except (OSError, ValueError, IndexError):
        try:
            import resource as _r
            ru = _r.getrusage(_r.RUSAGE_SELF)
            return ru.ru_utime + ru.ru_stime
        except Exception:  # noqa: BLE001
            return 0.0


def read_thread_cpu_seconds() -> float:
    """Current-thread CPU seconds — ``getrusage(RUSAGE_THREAD)`` (方案 §2).

    This is the精确 per-stage CPU source: thread-scoped, so concurrent tasks in
    other worker threads never contaminate the reading.
    """
    try:
        import resource as _r
        ru = _r.getrusage(_r.RUSAGE_THREAD)
        return ru.ru_utime + ru.ru_stime
    except (ValueError, AttributeError, OSError):
        # RUSAGE_THREAD is Linux-only; fall back to a per-thread clock so a stage
        # still gets *some* cpu number on other platforms (process-wide otherwise).
        try:
            return time.thread_time()
        except (AttributeError, OSError):
            return 0.0


def read_arrow_mb() -> float:
    """Bytes Arrow currently has allocated (process-level ledger), in MB."""
    try:
        import pyarrow as pa
        return pa.total_allocated_bytes() / 1048576.0
    except Exception:  # noqa: BLE001 - pyarrow optional / not yet imported
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class _TaskCtx:
    task_id: str
    run_id: str | None
    started: float
    thread_ident: int | None = None
    stage: str | None = None
    # admission ledger for this task (方案 §4.1 admission node) — best-effort,
    # populated by the dispatcher when the governance gate defers/grants.
    admission: list[dict] = field(default_factory=list)


class InFlightRegistry:
    """Process-global, thread-safe map of in-flight tasks (方案 §2/§3.5).

    The dispatcher marks tasks in/out; each worker thread binds its own
    ``thread_ident`` and writes its active stage. The sampler reads the snapshot
    each tick; the profiler reads ``thread_ident`` to extract just the target
    task's stack.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, _TaskCtx] = {}
        # peak concurrency observed while a given run was in flight (run_id → N).
        self._peak_conc: dict[str, int] = {}

    def mark_inflight(self, task_id: str, run_id: str | None) -> None:
        with self._lock:
            ctx = self._tasks.get(task_id)
            if ctx is None:
                # preserve a thread_ident the worker may have already bound (the
                # dispatcher marks inflight and the worker binds its thread in a
                # race-free order, but never clobber if it got there first).
                self._tasks[task_id] = _TaskCtx(task_id, run_id, time.time())
            else:
                ctx.run_id = run_id or ctx.run_id
            self._bump_peaks_locked()

    def bind_thread(self, task_id: str, thread_ident: int) -> None:
        with self._lock:
            ctx = self._tasks.get(task_id)
            if ctx is None:
                ctx = _TaskCtx(task_id, None, time.time())
                self._tasks[task_id] = ctx
            ctx.thread_ident = thread_ident

    def bind_run(self, task_id: str, run_id: str | None) -> None:
        with self._lock:
            ctx = self._tasks.get(task_id)
            if ctx is None:
                ctx = _TaskCtx(task_id, run_id, time.time())
                self._tasks[task_id] = ctx
            elif run_id:
                ctx.run_id = run_id
            self._bump_peaks_locked()

    def set_stage(self, task_id: str, stage: str | None) -> None:
        # a stage change never alters the in-flight count, so peaks need no re-bump.
        with self._lock:
            ctx = self._tasks.get(task_id)
            if ctx is not None:
                ctx.stage = stage

    def record_admission(self, task_id: str, event: dict) -> None:
        with self._lock:
            ctx = self._tasks.get(task_id)
            if ctx is not None:
                ctx.admission.append(event)

    def get_admission(self, task_id: str) -> list[dict]:
        """Read a task's admission ledger without clearing it (worker side)."""
        with self._lock:
            ctx = self._tasks.get(task_id)
            return list(ctx.admission) if ctx else []

    def clear_inflight(self, task_id: str) -> list[dict]:
        with self._lock:
            ctx = self._tasks.pop(task_id, None)
            return list(ctx.admission) if ctx else []

    def thread_ident_of(self, task_id: str) -> int | None:
        with self._lock:
            ctx = self._tasks.get(task_id)
            return ctx.thread_ident if ctx else None

    def stage_of(self, task_id: str) -> str | None:
        with self._lock:
            ctx = self._tasks.get(task_id)
            return ctx.stage if ctx else None

    def peak_concurrency(self, run_id: str) -> int:
        with self._lock:
            return self._peak_conc.get(run_id, 1)

    def reset_peak(self, run_id: str) -> None:
        with self._lock:
            self._peak_conc.pop(run_id, None)

    def _bump_peaks_locked(self) -> None:
        n = len(self._tasks)
        for ctx in self._tasks.values():
            if ctx.run_id is None:
                continue
            cur = self._peak_conc.get(ctx.run_id, 1)
            if n > cur:
                self._peak_conc[ctx.run_id] = n

    def snapshot(self) -> tuple[int, list[str], list[str]]:
        """(in_flight count, run_ids, active stage labels) — sampler reads this."""
        with self._lock:
            run_ids = [c.run_id for c in self._tasks.values() if c.run_id]
            stages = [c.stage for c in self._tasks.values() if c.stage]
            return len(self._tasks), run_ids, stages

    def current_count(self) -> int:
        with self._lock:
            return len(self._tasks)

    def reset(self) -> None:
        """Drop all in-flight bookkeeping (test helper / hard recovery)."""
        with self._lock:
            self._tasks.clear()
            self._peak_conc.clear()


# Process-global singleton — the dispatcher, worker threads and sampler all share
# this one instance (same process). The API process never touches it.
REGISTRY = InFlightRegistry()


# ─────────────────────────────────────────────────────────────────────────────
class _NdjsonWriter:
    """Append-only daily NDJSON with size rollover + retention (方案 §3.1/§9)."""

    def __init__(self, settings: Settings) -> None:
        self._dir = settings.worker_samples_dir
        self._max_bytes = settings.resource_ndjson_max_mb * 1048576
        self._retention_days = settings.resource_ndjson_retention_days
        self._fh = None
        self._day = ""
        self._roll = 0
        self._last_sweep = 0.0

    def _path(self, day: str, roll: int) -> str:
        suffix = "" if roll == 0 else f".{roll}"
        return os.path.join(self._dir, f"{day}{suffix}.ndjson")

    def _open_for(self, day: str) -> None:
        os.makedirs(self._dir, exist_ok=True)
        roll = 0
        while True:
            p = self._path(day, roll)
            if not os.path.exists(p) or os.path.getsize(p) < self._max_bytes:
                break
            roll += 1
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:  # noqa: BLE001
                pass
        self._fh = open(self._path(day, roll), "a", buffering=1, encoding="utf-8")
        self._day, self._roll = day, roll

    def write(self, line: str) -> None:
        day = time.strftime("%Y%m%d", time.gmtime())
        if self._fh is None or day != self._day:
            self._open_for(day)
            self._sweep_retention()
        elif self._fh.tell() >= self._max_bytes:
            self._open_for(day)
        self._fh.write(line + "\n")

    def _sweep_retention(self) -> None:
        now = time.time()
        if now - self._last_sweep < 3600:
            return
        self._last_sweep = now
        cutoff = now - self._retention_days * 86400
        try:
            for name in os.listdir(self._dir):
                if not name.endswith(".ndjson"):
                    continue
                p = os.path.join(self._dir, name)
                try:
                    if os.path.getmtime(p) < cutoff:
                        os.remove(p)
                except OSError:
                    pass
        except OSError:
            pass

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except Exception:  # noqa: BLE001
                pass
            self._fh = None


# ─────────────────────────────────────────────────────────────────────────────
class ResourceSampler:
    """1Hz process sampler thread (方案 §3.1). Fail-safe: never sinks a task."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._s = settings or get_settings()
        interval = max(0.05, float(self._s.resource_sample_interval_s))
        self._interval = interval
        # 2h ring buffer at the configured cadence.
        self._cap = max(16, int(2 * 3600 / interval))
        self._buf: deque[dict] = deque(maxlen=self._cap)
        self._buf_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._writer = _NdjsonWriter(self._s)
        self._alive = False
        self._last_cpu = read_proc_cpu_seconds()
        self._last_wall = time.time()
        self._ncpu = _ncpu()
        self._warm = False   # first tick only seeds the CPU baseline (no rate yet)

    # ── lifecycle ────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread is not None:
            return
        self._alive = True
        self._thread = threading.Thread(
            target=self._loop, name="resource-sampler", daemon=True
        )
        self._thread.start()
        _log.info("resource sampler up — %.0fHz ring=%d",
                  1.0 / self._interval, self._cap)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._writer.close()
        # 对称收尾: 清掉死线程引用并复位 stop 事件, 让同一实例可被 start() 干净重启
        # (否则 start() 的 `if self._thread is not None: return` 会让重启永久静默 no-op).
        self._thread = None
        self._alive = False
        self._stop.clear()

    def alive(self) -> bool:
        return self._alive and (self._thread is not None and self._thread.is_alive())

    # ── the loop ─────────────────────────────────────────────────────────
    def _loop(self) -> None:
        while not self._stop.is_set():
            t0 = time.time()
            try:
                self._tick()
            except Exception:  # noqa: BLE001 - 失败安全红线: warn only, never raise
                _log.warning("resource sampler tick failed (non-fatal)", exc_info=True)
            # account for tick cost so cadence stays ~1Hz under light load
            elapsed = time.time() - t0
            self._stop.wait(max(0.0, self._interval - elapsed))
        self._alive = False

    def _tick(self) -> None:
        now = time.time()
        rss = read_rss_mb()
        arrow = read_arrow_mb()
        cpu_now = read_proc_cpu_seconds()
        dt_wall = max(1e-6, now - self._last_wall)
        # process CPU% (单核口径, 可 >100); the first tick has no prior interval so
        # it只 seeds the baseline. Cap at the physical ceiling (100×核数) so a tiny
        # cold-start dt_wall can't yield an absurd percentage.
        if self._warm:
            cpu_pct = max(0.0, min((cpu_now - self._last_cpu) / dt_wall * 100.0,
                                   100.0 * self._ncpu))
        else:
            cpu_pct = 0.0
            self._warm = True
        self._last_cpu, self._last_wall = cpu_now, now
        n, run_ids, stages = REGISTRY.snapshot()
        sample = {
            "ts": round(now, 3),
            "rss_mb": round(rss, 1),
            "cpu_pct": round(cpu_pct, 1),
            "arrow_mb": round(arrow, 1),
            "in_flight": n,
            "tasks": run_ids,
            "stages": stages,
        }
        with self._buf_lock:
            self._buf.append(sample)
        try:
            import json
            self._writer.write(json.dumps(sample, separators=(",", ":")))
        except Exception:  # noqa: BLE001 - NDJSON write is best-effort
            _log.debug("ndjson write failed", exc_info=True)

    # ── slice for a finished run (方案 §3.3) ──────────────────────────────
    def slice(self, start_ts: float, end_ts: float) -> list[dict]:
        """Samples whose ts ∈ [start, end], chronological. Drains the ring buffer
        copy — the峰值绝不被平均抹掉 downsample happens downstream (max-pool)."""
        with self._buf_lock:
            rows = [dict(s) for s in self._buf]
        # pad the window slightly so a sub-second run still catches its samples
        lo, hi = start_ts - self._interval, end_ts + self._interval
        return [s for s in rows if lo <= s["ts"] <= hi]

    def latest(self) -> dict | None:
        with self._buf_lock:
            return dict(self._buf[-1]) if self._buf else None


def _ncpu() -> int:
    try:
        return max(1, len(os.sched_getaffinity(0)))  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 1)


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton + thin accessors used across the worker process.
_SAMPLER: ResourceSampler | None = None


def start_sampler(settings: Settings | None = None) -> ResourceSampler | None:
    """Start the process sampler (called by the dispatcher). No-op when disabled."""
    global _SAMPLER
    s = settings or get_settings()
    if not s.resource_sampling_enabled:
        return None
    if _SAMPLER is None:
        _SAMPLER = ResourceSampler(s)
        try:
            _SAMPLER.start()
        except Exception:  # noqa: BLE001 - sampler must never crash the worker
            _log.warning("resource sampler failed to start (non-fatal)", exc_info=True)
            _SAMPLER = None
    return _SAMPLER


def stop_sampler() -> None:
    global _SAMPLER
    if _SAMPLER is not None:
        _SAMPLER.stop()
        _SAMPLER = None


def get_sampler() -> ResourceSampler | None:
    return _SAMPLER


def sampler_alive() -> bool:
    return _SAMPLER is not None and _SAMPLER.alive()


def slice_samples(start_ts: float, end_ts: float) -> list[dict]:
    return _SAMPLER.slice(start_ts, end_ts) if _SAMPLER is not None else []
