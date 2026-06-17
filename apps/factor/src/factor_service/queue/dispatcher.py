"""Worker entry point: `python -m factor_service.queue.dispatcher`.

A single dispatcher polls the `tasks` table every FACTOR_POLL_INTERVAL_MS and
submits eligible work to a thread pool of FACTOR_CONCURRENCY slots. Each task
runs in its own thread inside the worker process; the executor stamps progress
via heartbeats so the API can show real-time status.

We use threads rather than processes because:
  - the data-layer DataReader holds a DuckDB connection that is cheaper per
    thread than per process
  - tasks are I/O + numpy heavy; the GIL is rarely the bottleneck
  - simpler shared-state (SQLite session pool) than child processes

The reserve_interactive setting prevents routine tasks from filling all
slots — when running_routine >= concurrency - reserve, we only pick from
the interactive lane.

内存治理接入 (《内存治理总体方案》):
  - WP5 接入点①: claim 前过 quant_admission 预算闸 — peek → try_acquire →
    Grant 才 claim;Deferred 跳过该任务,排干模式整轮停手 (只出不进)。
    取代/吸收 PR #41 的 FACTOR_HEAVY_CONCURRENCY 过渡限速。
  - WP6: 进程级线程配额 (POLARS_MAX_THREADS / OMP_NUM_THREADS) 必须在
    polars/numpy 首次 import 之前写入 env — 故放在本模块最顶部。
"""
from __future__ import annotations

import os


def _setup_thread_quota() -> None:
    """CPU 配额 (WP6): polars 线程池与 BLAS 在 import 时定容,只能用 env 配。

    取值 = 可用核 ÷ 重任务槽数 (与 DuckDB PRAGMA threads 同公式);已显式设置
    的 env 不覆盖 (compose 注入优先)。必须先于任何 polars/numpy import 执行。
    """
    try:
        cores = max(1, len(os.sched_getaffinity(0)))  # type: ignore[attr-defined]
    except AttributeError:  # macOS dev
        cores = max(1, os.cpu_count() or 1)
    slots = int(os.environ.get("QUANT_HEAVY_TASK_SLOTS",
                               os.environ.get("FACTOR_CONCURRENCY", "4")))
    per_task = str(max(1, cores // max(1, slots)))
    os.environ.setdefault("POLARS_MAX_THREADS", per_task)
    os.environ.setdefault("OMP_NUM_THREADS", per_task)


_setup_thread_quota()

import logging  # noqa: E402
import signal  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from concurrent.futures import Future, ThreadPoolExecutor  # noqa: E402

from quant_admission import AdmissionGate, Deferred, Grant  # noqa: E402
from quant_data import config as qd_config  # noqa: E402
from quant_data import logging as qd_logging  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from ..config import ensure_dirs, get_settings  # noqa: E402
from ..db import base as _db_base  # noqa: E402
from ..db.models import Task  # noqa: E402
from ..db.seed import seed_all  # noqa: E402
from ..logging_ctx import configure_logging, trace_id_var  # noqa: E402
from ..obs import install_obs  # noqa: E402
from ..services.resource_sampler import (  # noqa: E402
    REGISTRY,
    sampler_alive,
    start_sampler,
    stop_sampler,
)
from ..services.run_executor import execute_task  # noqa: E402
from . import store  # noqa: E402
from .estimator import update_stats  # noqa: E402


_log = logging.getLogger("factor.dispatcher")
# Structured logger: task.claimed is bound to the task's own trace_id so the
# "claimed" moment joins the same app_log chain as the run that enqueued it.
_qlog = qd_logging.get_logger("factor.dispatcher")
_stop = threading.Event()

# 任务类型 → 准入预算类 (内存治理 WP5; 预算表见 ADMISSION_BUDGETS_JSON)。
# 未列出的类型按 factor.task.light 小预算记账 (排干期一并暂停)。
_ADMISSION_CLASS = {
    "evaluation": "factor.eval.daily",
    "basket_eval": "factor.eval.daily",
    "correlation": "factor.correlation",
    "basket_corr": "factor.correlation",
}


def _admission_class(task_type: str) -> str:
    return _ADMISSION_CLASS.get(task_type, "factor.task.light")


def _handle_signal(signum, _frame):  # noqa: ARG001
    _log.warning("dispatcher received signal %s; will stop after in-flight tasks", signum)
    _stop.set()


def _run_one(task_id: str, trace_id: str | None, exclusive: bool = False) -> dict:
    tok = trace_id_var.set(trace_id or "-")
    # bind quant_data's context too so the task's data-layer reads carry the
    # task's trace_id into app_log (V3 §6.4 — autonomous/worker task tracing).
    qd_tok = qd_logging.bind_context(trace_id=trace_id)
    # 独占 CPU 提升 (WP5 v1.2 可选项): an exclusive grant runs alone — its
    # DuckDB connections may take the full core set (per-connection PRAGMA;
    # polars' process pool is import-frozen and can't be raised, by design).
    thr_tok = qd_config.set_duckdb_thread_override(
        qd_config.available_cores() if exclusive else None
    )
    try:
        return execute_task(task_id)
    finally:
        qd_config.reset_duckdb_thread_override(thr_tok)
        qd_logging.reset_context(qd_tok)
        trace_id_var.reset(tok)


def _advance_basket(db: Session, t: Task) -> None:
    """The ONLY orchestration hook in the dispatcher (因子层重构 §6.3): after a
    basket child finalizes, push its parent run forward. Never crash the loop."""
    if not getattr(t, "brun_id", None):
        return
    try:
        from ..baskets import orchestrator
        orchestrator.advance(db, t.brun_id)
    except Exception:  # noqa: BLE001
        _log.warning("orchestrator.advance failed for brun %s", t.brun_id, exc_info=True)


def _finalize_future(db: Session, task_id: str, fut: Future) -> None:
    t = db.get(Task, task_id)
    if t is None:
        return
    if fut.exception() is not None:
        exc = fut.exception()
        msg = f"{type(exc).__name__}: {exc}"
        # Auto-retry within max_attempts (best-effort)
        if t.attempt < t.max_attempts:
            t.status = "queued"
            t.worker_id = None
            t.heartbeat_at = None
            t.started_at = None
        else:
            t.status = "failed"
            t.error = msg
            t.finished_at = t.finished_at or _now_utc()
        db.commit()
        _log.error("task %s failed: %s", task_id, msg)
    else:
        res = fut.result() or {}
        # The worker returns the *run* outcome in res["status"]; a preflight
        # block or cancel returns normally (no exception) but the run did NOT
        # succeed, so reflect that on the task rather than marking it success.
        run_status = res.get("status")
        if run_status == "failed":
            t.status = "failed"
            t.error = res.get("reason") or t.error or "run_failed"
        elif run_status == "canceled":
            t.status = "canceled"
        elif run_status in ("missing", "unknown_type"):
            # 没有真正干活: Task 行中途消失 / task_type 损坏 —— 绝不能记成
            # success (否则 today_done 虚高, 掩盖丢数据或配置错). 记失败。
            t.status = "failed"
            t.error = res.get("reason") or run_status
        else:
            t.status = "success"  # success or degraded (the run completed)
        t.finished_at = t.finished_at or _now_utc()
        t.result_summary = res.get("summary") or t.result_summary or {}
        db.commit()
        # update duration history
        if t.started_at and t.finished_at:
            dur = (t.finished_at - t.started_at).total_seconds()
            update_stats(db, t.task_type, t.payload or {}, max(0.1, dur))
        _log.info(
            "task %s success in %.1fs",
            task_id,
            (t.finished_at - t.started_at).total_seconds()
            if (t.started_at and t.finished_at)
            else 0.0,
        )
    # Push the owning basket run forward (no-op for standalone tasks).
    _advance_basket(db, t)


def _now_utc():
    import datetime as dt

    return dt.datetime.utcnow()


def main() -> None:
    s = get_settings()
    configure_logging(s.log_level)
    install_obs(s)               # log shipper → web /logs/ingest (source="factor")
    ensure_dirs(s)
    # 资源画像进程采样器 (资源画像方案 §3.1): 随 dispatcher 启动, 1Hz 采进程 RSS/CPU/
    # Arrow/in_flight; 失败安全 — 采样器异常绝不影响任务调度.
    start_sampler(s)
    _db_base.init_db()
    # Seed builtins + presets on first launch (idempotent).
    with _db_base.SessionLocal() as db:
        seed_all(db)

    conc = int(os.getenv("FACTOR_CONCURRENCY", str(s.factor_concurrency)))
    reserve = int(
        os.getenv("FACTOR_RESERVE_INTERACTIVE", str(s.factor_reserve_interactive))
    )
    poll_ms = int(os.getenv("FACTOR_POLL_INTERVAL_MS", str(s.factor_poll_interval_ms)))
    stale_sec = int(s.factor_stale_seconds)

    worker_id = (os.environ.get("HOSTNAME", "worker") + f"#{os.getpid()}")
    _log.info(
        "dispatcher up — concurrency=%d reserve=%d poll=%dms worker=%s",
        conc,
        reserve,
        poll_ms,
        worker_id,
    )

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    pool = ThreadPoolExecutor(max_workers=conc, thread_name_prefix="factor-worker")
    inflight: dict[Future, str] = {}
    # 内存准入闸 (WP5 接入点①): per-process gate; grants released on finalize.
    # fail-fast 校验只对本进程会申请的任务类负责 (预算表全局共享下发, T-WP5-008)。
    gate = AdmissionGate.from_env(validate_budgets=(
        *set(_ADMISSION_CLASS.values()), "factor.task.light"))
    grants: dict[str, Grant] = {}
    # 资源画像 admission 台账 (方案 §4.1): buffer deferrals per task so the grant
    # event can carry an accurate wait_ms. Bounded by the queue; pruned at grant.
    adm_log: dict[str, list[dict]] = {}
    adm_defer_first: dict[str, float] = {}

    db = _db_base.SessionLocal()
    store.recover_stale(db, stale_sec)

    # 因子篮子调度 (§7.1) + 孤儿巡检 (§6.3) — throttled to ~once/60s.
    _last_basket_sweep = [0.0]
    _last_sampler_warn = [0.0]   # 资源画像采样器死亡告警节流

    def _basket_maintenance() -> None:
        if not s.factor_basket_enabled:
            return
        now = time.monotonic()
        if now - _last_basket_sweep[0] < 60.0:
            return
        _last_basket_sweep[0] = now
        try:
            from ..baskets import scheduler as _sched
            from quant_data import DataReader
            _sched.sweep_orphans(db)
            reader = DataReader(root=s.quant_data_root)
            _sched.tick_baskets(db, reader)
        except Exception:  # noqa: BLE001 - maintenance must never crash the loop
            _log.debug("basket maintenance failed", exc_info=True)

    try:
        while not _stop.is_set():
            # 1) collect finished futures (+ release their admission grants)
            done = [f for f in list(inflight) if f.done()]
            for f in done:
                tid = inflight.pop(f)
                g = grants.pop(tid, None)
                if g is not None:
                    g.release()
                # 资源画像: drop the task from the in-flight registry (symmetric
                # with mark_inflight) so concurrent-attribution stays accurate.
                REGISTRY.clear_inflight(tid)
                adm_log.pop(tid, None)
                adm_defer_first.pop(tid, None)
                _finalize_future(db, tid, f)

            # 2) recover stale running tasks (heartbeat gap)
            store.recover_stale(db, stale_sec)

            # 3) fill up to concurrency — peek → admission gate → claim (WP5).
            #    Deferred ⇒ the task stays queued untouched; draining ⇒ 只出
            #    不进, stop filling this round (the queue-head waiter is re-
            #    offered every poll and goes first when the floor is clear).
            deferred_ids: set[str] = set()
            while len(inflight) < conc:
                running_routine = (
                    db.query(Task)
                    .filter(Task.status == "running", Task.lane == "routine")
                    .count()
                )
                allow_routine = running_routine < (conc - reserve)
                cand = store.peek_next_filtered(db, allow_routine, exclude_ids=deferred_ids)
                if cand is None:
                    break
                # 安全审查 P2: 我们已持有该 task 的 grant(其 future 仍在跑,但
                # recover_stale 因心跳过期把它翻回 queued 又被 peek 到)⇒ 不得二次
                # 准入。二次 grant 会按 task_id 覆盖 _in_flight 记账,使闸把两份
                # 在跑内存只算一份(正是 06-04 卡死场景),削弱 OOM 防护。跳过它,
                # 等当前 future 收尾时 _finalize_future 统一处理。
                if cand.task_id in grants:
                    deferred_ids.add(cand.task_id)
                    continue
                verdict = gate.try_acquire(
                    _admission_class(cand.task_type), task_id=cand.task_id
                )
                if isinstance(verdict, Deferred):
                    deferred_ids.add(cand.task_id)
                    # 资源画像 admission 台账: buffer the defer (wait_ms filled at grant).
                    now_w = time.time()
                    adm_defer_first.setdefault(cand.task_id, now_w)
                    log = adm_log.setdefault(cand.task_id, [])
                    if len(log) < 10:
                        log.append({"ts": now_w,
                                    "event": "draining" if verdict.draining else "deferred",
                                    "wait_ms": None})
                    if verdict.draining:
                        break  # 排干模式: 本轮整体停手
                    continue
                t = store.claim_task(db, cand.task_id, worker_id)
                if t is None:  # stale peek (cancelled/raced) — return the budget
                    verdict.release()
                    deferred_ids.add(cand.task_id)
                    continue
                grants[t.task_id] = verdict
                # 资源画像: register the task in-flight BEFORE submit so the sampler
                # counts it and the worker can bind its thread; flush admission台账.
                REGISTRY.mark_inflight(t.task_id, t.run_id)
                now_w = time.time()
                first = adm_defer_first.pop(t.task_id, None)
                events = adm_log.pop(t.task_id, [])
                events.append({"ts": now_w, "event": "granted",
                               "wait_ms": int((now_w - first) * 1000) if first else 0})
                for ev in events:
                    REGISTRY.record_admission(t.task_id, ev)
                fut = pool.submit(_run_one, t.task_id, t.trace_id, verdict.exclusive)
                inflight[fut] = t.task_id
                _log.info(
                    "claimed task %s (type=%s lane=%s admission=%s%s)",
                    t.task_id,
                    t.task_type,
                    t.lane,
                    _admission_class(t.task_type),
                    " exclusive" if verdict.exclusive else "",
                )
                # structured twin, joined to the task's trace_id (V3 §4.3 / §6.4)
                _tok = qd_logging.bind_context(trace_id=t.trace_id)
                try:
                    _qlog.info("task.claimed", task_id=t.task_id, type=t.task_type,
                               lane=t.lane, trace_id=t.trace_id)
                finally:
                    qd_logging.reset_context(_tok)

            # basket tracking tick + orphan sweep (throttled inside).
            _basket_maintenance()

            # liveness beacon (修复 A1 §3.4): mark this dispatcher alive every cycle —
            # even idle — so factor-api /health can tell live-but-idle from crashed.
            try:
                store.record_worker_heartbeat(db, worker_id)
            except Exception:  # noqa: BLE001 - heartbeat must never crash the loop
                _log.debug("worker heartbeat write failed", exc_info=True)

            # 资源画像采样器死亡上报 (方案 §3.1): 降级标记 — 画像缺失但任务照跑.
            now_m = time.monotonic()
            if s.resource_sampling_enabled and not sampler_alive() \
                    and now_m - _last_sampler_warn[0] > 300:
                _last_sampler_warn[0] = now_m
                _log.warning("resource sampler not alive — 资源画像时间线将降级缺失")

            # 资源画像 admission 台账 TTL 清理: 被延迟后又取消的任务不会再被 peek,
            # 其条目永不经 grant/finalize 清理 — 超 1h 直接丢弃, 防按任务数泄漏.
            _adm_now = time.time()
            for _tid in [k for k, ts in adm_defer_first.items() if _adm_now - ts > 3600]:
                adm_defer_first.pop(_tid, None)
                adm_log.pop(_tid, None)

            time.sleep(poll_ms / 1000.0)
    finally:
        _log.info("dispatcher shutting down; %d in-flight", len(inflight))
        stop_sampler()
        pool.shutdown(wait=True)
        db.close()


if __name__ == "__main__":
    main()
