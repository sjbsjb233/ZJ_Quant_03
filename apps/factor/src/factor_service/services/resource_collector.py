"""每次运行的阶段级资源采集 (资源画像方案 §3.2).

:class:`ResourceCollector` 在阶段始末采精确 CPU 秒 (``RUSAGE_THREAD``)、进程 RSS、
Arrow 物化增量,镜像现有 ``RunTrace.stage()/finish_stage()`` 的 begin/end 形态,
最小侵入接入 ``run_executor`` 与 ``basket_executor``。

红线:只旁路记录,绝不参与数值计算;任何采集异常被吞掉 (warn),运行照常。
``in_flight_max`` 不在此处实时统计 —— 收尾时由 ``resource_writer`` 用采样器切片
按阶段时间窗回填,口径与时间线一致。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from .resource_sampler import (
    REGISTRY,
    read_arrow_mb,
    read_rss_mb,
    read_thread_cpu_seconds,
)

_log = logging.getLogger("factor.resource_collector")


@dataclass
class _StageHandle:
    stage: str
    cpu0: float
    rss0: float
    arrow0: float
    from_ts: float


@dataclass
class StageResource:
    stage: str
    cpu_sec: float
    rss_start_mb: float
    rss_end_mb: float
    arrow_delta_mb: float
    from_ts: float
    to_ts: float
    in_flight_max: int = 1


@dataclass
class ResourceCollector:
    """One per run. Holds the run's identity + accumulated per-stage resources."""

    task_id: str
    run_id: str
    factor_id: str
    version: str = ""
    t0_wall: float = field(default_factory=time.time)
    stages: list[StageResource] = field(default_factory=list)

    def __post_init__(self) -> None:
        import threading
        if self.run_id:
            REGISTRY.reset_peak(self.run_id)
        # Bind this worker thread + run_id so the sampler counts us as in-flight
        # and the profiler can find our stack (方案 §2/§3.5).
        try:
            REGISTRY.bind_thread(self.task_id, threading.get_ident())
            REGISTRY.bind_run(self.task_id, self.run_id)
        except Exception:  # noqa: BLE001
            pass

    # ── stage hooks (mirror trace.stage/finish_stage) ─────────────────────
    def begin(self, stage: str) -> _StageHandle | None:
        try:
            REGISTRY.set_stage(self.task_id, stage)
            return _StageHandle(
                stage=stage,
                cpu0=read_thread_cpu_seconds(),
                rss0=read_rss_mb(),
                arrow0=read_arrow_mb(),
                from_ts=time.time(),
            )
        except Exception:  # noqa: BLE001 - never let采集 sink a stage
            _log.debug("resource stage begin failed", exc_info=True)
            return None

    def end(self, handle: _StageHandle | None, trace_rec=None) -> None:
        if handle is None:
            return
        try:
            cpu1 = read_thread_cpu_seconds()
            rss1 = read_rss_mb()
            arrow1 = read_arrow_mb()
            rec = StageResource(
                stage=handle.stage,
                cpu_sec=max(0.0, cpu1 - handle.cpu0),
                rss_start_mb=handle.rss0,
                rss_end_mb=rss1,
                arrow_delta_mb=arrow1 - handle.arrow0,
                from_ts=handle.from_ts,
                to_ts=time.time(),
            )
            self.stages.append(rec)
            REGISTRY.set_stage(self.task_id, None)
            # Stamp the trace StageRecord too, so trace.json's stages carry the
            # resource fields (方案 §3.2 StageRecord extension).
            if trace_rec is not None:
                trace_rec.cpu_sec = round(rec.cpu_sec, 3)
                trace_rec.rss_start_mb = round(rec.rss_start_mb, 1)
                trace_rec.rss_end_mb = round(rec.rss_end_mb, 1)
                trace_rec.arrow_delta_mb = round(rec.arrow_delta_mb, 1)
        except Exception:  # noqa: BLE001
            _log.debug("resource stage end failed", exc_info=True)
