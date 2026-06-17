"""Execute a single task — evaluation / correlation / import_validate.

Called from the dispatcher's thread pool. Picks up payload from `tasks`,
hits the engine, writes results to disk + factor.db, updates progress &
heartbeats so the UI shows live state.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import time
from typing import Any

import polars as pl
from quant_data import DataReader
from quant_data import logging as qd_logging
from quant_factor import (
    EvalConfig,
    FactorEvaluator,
    correlation_matrix,
)
from quant_factor.base import RunCancelled
from quant_factor.preflight import (
    PreflightBlocked,
    check_result_to_json,
    run_preflight,
)
from quant_factor.trace import RunTrace
from quant_factor.universe import resolve_universe
from quant_factor.registry import all_registered, instantiate, instantiate_from_code

from ..config import get_settings
from ..db import base as _db_base
from ..db.models import (
    EvalSummary,
    FactorRecord,
    FactorRun,
    FactorRunDegradation,
    FactorVersion,
    Task,
    Universe,
)
from ..queue import store
from .results_writer import (
    write_correlation_cache,
    write_degradations,
    write_run_results,
    write_run_trace,
)
from . import resource_writer
from .resource_collector import ResourceCollector
from .resource_profiler import RunProfiler
from .resource_sampler import REGISTRY


_log = logging.getLogger("factor.executor")
# Structured data-layer logger: emits as source="factor" with the worker's
# re-bound trace_id (dispatcher._run_one), so a run's full chain — run.start →
# run.stage.* → run.done — lands in app_log joined to the submitting request
# (容器化重构 V3 §4.3). No-op when no shipper sink is installed (standalone dev).
_qlog = qd_logging.get_logger("factor.executor")


def _open_run_log(run_id: str | None) -> tuple:
    if not run_id:
        return None, lambda m: None
    s = get_settings()
    os.makedirs(s.runs_dir, exist_ok=True)
    path = os.path.join(s.runs_dir, f"{run_id}.log")
    f = open(path, "a", buffering=1, encoding="utf-8")  # line-buffered

    def write(msg: str) -> None:
        ts = dt.datetime.utcnow().isoformat(timespec="milliseconds") + "Z"
        f.write(f"{ts}  INFO  {msg}\n")

    return f, write


def execute_task(task_id: str) -> dict[str, Any]:
    """Top-level worker — dispatches by task.type."""
    db = _db_base.SessionLocal()
    try:
        t = db.get(Task, task_id)
        if t is None:
            return {"task_id": task_id, "status": "missing"}
        if t.task_type == "evaluation":
            return _run_evaluation(db, t)
        if t.task_type == "correlation":
            return _run_correlation(db, t)
        if t.task_type == "import_validate":
            return _run_import_validate(db, t)
        if t.task_type in ("basket_eval", "basket_corr"):
            # Lazy import breaks the run_executor ↔ basket_executor cycle.
            from . import basket_executor
            if t.task_type == "basket_eval":
                return basket_executor.run_basket_eval(db, t)
            return basket_executor.run_basket_corr(db, t)
        return {"task_id": task_id, "status": "unknown_type"}
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
def _run_evaluation(db, t: Task) -> dict[str, Any]:
    run_id = t.run_id or t.payload.get("run_id")
    if not run_id:
        raise ValueError("evaluation task has no run_id")

    run = db.get(FactorRun, run_id)
    if run is None:
        raise ValueError(f"FactorRun {run_id} not found")

    run.status = "running"
    run.started_at = dt.datetime.utcnow()
    db.commit()

    t0 = time.perf_counter()
    stage = "load"
    _qlog.info("run.start", run_id=run_id, factor_id=run.factor_id, version=run.version)

    settings = get_settings()
    # 运行透视 旁路采集器 (设计 §2.3). None ⇒ no埋点 / no artifact / endpoint空态.
    trace = (
        RunTrace(run_id=run_id, histogram_enabled=settings.trace_histogram_enabled)
        if settings.trace_enabled
        else None
    )
    # 资源画像 (资源画像方案 §3.2/§3.5): 阶段级 CPU/内存采集器 + 可选函数级剖析器.
    # 全部旁路、失败安全 — 任何采集异常绝不影响 run 终态.
    collector = (
        ResourceCollector(task_id=t.task_id, run_id=run_id,
                          factor_id=run.factor_id, version=run.version)
        if settings.resource_sampling_enabled else None
    )
    profile_requested = bool((t.payload or {}).get("profile")) or settings.resource_profile_default
    profiler = None
    if profile_requested:
        try:
            import threading as _th
            REGISTRY.bind_thread(t.task_id, _th.get_ident())
            profiler = RunProfiler(t.task_id, run_id, settings)
            profiler.start()
        except Exception:  # noqa: BLE001 - 剖析失败绝不影响任务
            _log.warning("profiler failed to start (non-fatal)", exc_info=True)
            profiler = None

    log_file, log_write = _open_run_log(run_id)
    try:
        # ── stage: load (resolve factor/version/universe + build the engine) ──
        st_load = trace.stage("load") if trace else None
        rc_load = collector.begin("load") if collector else None
        t_load = time.perf_counter()
        with qd_logging.timed(_qlog, "run.stage.load", run_id=run_id) as m:
            # Resolve factor + version + config
            factor_id = run.factor_id
            version = run.version
            params = run.params or {}
            cfg = EvalConfig.from_dict(run.run_config)

            rec = db.get(FactorRecord, factor_id)
            ver = (
                db.query(FactorVersion)
                .filter(FactorVersion.factor_id == factor_id, FactorVersion.version == version)
                .first()
            )
            if rec is None or ver is None:
                raise ValueError(
                    f"factor/{factor_id} version {version} not in registry"
                )
            if not ver.enabled:
                raise PermissionError(
                    f"factor version {factor_id}@{version} is not enabled"
                )

            log_write(f"resolved factor {factor_id} @ {version} from {ver.class_name}")
            if ver.code_path and os.path.exists(ver.code_path):
                with open(ver.code_path, encoding="utf-8") as fh:
                    src = fh.read()
                factor = instantiate_from_code(src, ver.class_name, params)
                log_write(f"loaded factor code from {ver.code_path}")
            else:
                factor = instantiate(rec.base_name, params)
                log_write(f"loaded builtin factor {rec.base_name}")

            # Universe def from factor.db
            uni_row = db.get(Universe, cfg.universe_id)
            if uni_row is None:
                raise ValueError(f"universe {cfg.universe_id} not found")
            uni_def = _universe_row_to_dict(uni_row)
            custom_syms = None
            if uni_row.kind == "custom":
                from ..db.models import UniverseMember

                custom_syms = [
                    r[0]
                    for r in db.query(UniverseMember.symbol)
                    .filter(UniverseMember.universe_id == uni_row.universe_id)
                    .all()
                ]
            log_write(
                f"universe {uni_row.universe_id} ({uni_row.kind}); "
                f"members={len(custom_syms) if custom_syms is not None else '∞'}"
            )

            # Engine with progress + cancel callbacks
            def progress(c, n):
                store.heartbeat(db, t.task_id, current=int(c), total=int(n))

            def cancelled():
                db.refresh(t)
                return bool(t.cancel_requested)

            def log_cb(msg: str):
                log_write(msg)

            reader = DataReader(root=get_settings().quant_data_root)
            m["universe_id"] = uni_row.universe_id
            m["n_symbols"] = len(custom_syms) if custom_syms is not None else None

        if trace:
            # Seed基础 meta so even a blocked/failed run shows a summary row.
            trace.meta.setdefault("factor", factor_id)
            trace.meta.update({
                "universe_id": uni_row.universe_id,
                "rebalance_freq": cfg.rebalance,
                "horizons": list(cfg.horizons),
                "primary_horizon": int(cfg.primary_horizon),
                "start": cfg.start.isoformat() if cfg.start else None,
                "end": cfg.end.isoformat() if cfg.end else None,
            })
            trace.finish_stage(
                st_load, status="ok",
                duration_ms=(time.perf_counter() - t_load) * 1000,
                factor_id=factor_id, version=version, class_name=ver.class_name,
                code_source=(
                    "code_path"
                    if (ver.code_path and os.path.exists(ver.code_path))
                    else "builtin"
                ),
                universe_id=uni_row.universe_id, universe_kind=uni_row.kind,
                member_count=(len(custom_syms) if custom_syms is not None else None),
                config_resolved=cfg.to_jsonable(),
            )
        if collector:
            collector.end(rc_load, st_load)

        # ── stage: preflight (NEW · 设计 §5/§7) ──────────────────────────────
        # The authoritative gate. A block here marks the run failed and it NEVER
        # computes — the core 红线 (设计 §13): industry/size data missing can
        # never produce status=success. A degrade seeds the run's台账.
        stage = "preflight"
        ledger = None
        st_pf = trace.stage("preflight") if trace else None
        rc_pf = collector.begin("preflight") if collector else None
        t_pf = time.perf_counter()
        with qd_logging.timed(_qlog, "run.stage.preflight", run_id=run_id) as m:
            if settings.preflight_enabled:
                symbols = _resolve_symbols_for_preflight(
                    reader, uni_def, custom_syms, cfg
                )
                policy_overrides = (run.run_config or {}).get("degradation_policy")
                try:
                    # `trace` lets run_preflight record the *full* checklist (含
                    # 通过/跳过) into st_pf.detail["checks"] before the gate decision.
                    _blocked, ledger = run_preflight(
                        reader, cfg, uni_def, symbols, cfg.start, cfg.end,
                        profile=settings.degradation_policy_profile,
                        policy_overrides=policy_overrides,
                        trace=trace,
                    )
                except PreflightBlocked as pb:
                    if trace:
                        trace.finish_stage(
                            st_pf, status="blocked",
                            duration_ms=(time.perf_counter() - t_pf) * 1000,
                            blocked=[r.check_id for r in pb.results],
                        )
                    if collector:
                        collector.end(rc_pf, st_pf)
                    if trace:
                        _write_trace_safe(run_id, trace)   # 阻断也落盘(关键!)
                    return _finish_preflight_blocked(
                        db, run, run_id, t, pb, log_write, t0
                    )
                m["degrade_count"] = ledger.count
                m["n_checked_symbols"] = len(symbols)
                if ledger.count:
                    log_write(
                        f"preflight: {ledger.count} 项降级 "
                        f"({[e.code for e in ledger.events]})"
                    )
            else:
                m["skipped"] = True

        if trace:
            if not settings.preflight_enabled:
                pf_status = "skipped"
            elif ledger and ledger.count:
                pf_status = "degrade"
            else:
                pf_status = "ok"
            trace.finish_stage(
                st_pf, status=pf_status,
                duration_ms=(time.perf_counter() - t_pf) * 1000,
            )
        if collector:
            collector.end(rc_pf, st_pf)

        # The evaluator carries the (preflight-seeded) ledger so runtime
        # degradations append to the same台账.
        from quant_factor.degradation import DegradationLedger

        if ledger is None:
            ledger = DegradationLedger()
        # Evaluator + orchestration share ONE RunTrace instance, so the逐日数据
        # (§2.2) and阶段/漏斗 (§2.3) land in the same object — no post-merge.
        evaluator = FactorEvaluator(
            reader,
            ledger=ledger,
            trace=trace,
            progress_cb=progress,
            log_cb=log_cb,
            cancel_cb=cancelled,
        )

        # ── stage: compute (run the factor + evaluation engine) ──────────────
        stage = "compute"
        st_compute = trace.stage("compute") if trace else None
        rc_compute = collector.begin("compute") if collector else None
        t_compute = time.perf_counter()
        with qd_logging.timed(_qlog, "run.stage.compute", run_id=run_id) as m:
            result = evaluator.evaluate(factor, cfg, uni_def, custom_syms)
            _ic = result.get("ic_series")
            ic_rows = int(_ic.height) if isinstance(_ic, pl.DataFrame) else 0
            m["ic_rows"] = ic_rows
        if trace:
            trace.finish_stage(
                st_compute,
                status="degrade" if ledger.count > 0 else "ok",
                duration_ms=(time.perf_counter() - t_compute) * 1000,
                ic_rows=ic_rows,
            )
        if collector:
            collector.end(rc_compute, st_compute)

        # ── stage: write (persist IC / layer / decay / turnover artifacts) ───
        stage = "write"
        st_write = trace.stage("write") if trace else None
        rc_write = collector.begin("write") if collector else None
        t_write = time.perf_counter()
        with qd_logging.timed(_qlog, "run.results.write", run_id=run_id) as m:
            write_run_results(run_id, factor_id, result)
            m["rows_written"] = ic_rows
            m["path"] = os.path.join(get_settings().evaluations_dir, factor_id)
        if trace:
            trace.finish_stage(
                st_write, status="ok",
                duration_ms=(time.perf_counter() - t_write) * 1000,
                rows_written=ic_rows,
                path=os.path.join(get_settings().evaluations_dir, factor_id),
            )
        if collector:
            collector.end(rc_write, st_write)

        # ── stage: eval (roll the metrics summary into eval_summary) ─────────
        stage = "eval"
        st_eval = trace.stage("eval") if trace else None
        rc_eval = collector.begin("eval") if collector else None
        t_eval = time.perf_counter()
        with qd_logging.timed(_qlog, "run.stage.eval", run_id=run_id) as m:
            _persist_eval_summary(db, run, cfg, uni_row.universe_id, result, ledger)
            m["metrics_n"] = len(result.get("summary") or {})
        if trace:
            trace.finish_stage(
                st_eval, status="ok",
                duration_ms=(time.perf_counter() - t_eval) * 1000,
                metrics_n=len(result.get("summary") or {}),
            )
        if collector:
            collector.end(rc_eval, st_eval)

        # ── status decision (设计 §7): degraded iff the台账 has ≥1 event ─────
        final_status = "degraded" if ledger.count > 0 else "success"
        run.status = final_status
        run.degradation_level = ledger.level()
        run.degradation_count = ledger.count
        run.has_critical = ledger.has_critical
        run.finished_at = dt.datetime.utcnow()
        run.duration_sec = (run.finished_at - run.started_at).total_seconds()
        run.summary = dict(result.get("summary") or {})
        _persist_degradation(db, run_id, ledger)
        db.commit()
        write_degradations(
            run_id, status=final_status, level=ledger.level(),
            events=ledger.to_jsonable(),
        )
        # 运行透视 落盘 — success / degraded terminal path.
        _write_trace_safe(run_id, trace)
        log_write(
            f"DONE ({final_status}; {ledger.count} 降级 · {ledger.level()}) "
            f"in {run.duration_sec:.1f}s"
        )
        _qlog.info("run.done", run_id=run_id, status=final_status,
                   degrade_count=ledger.count, degrade_level=ledger.level(),
                   has_critical=ledger.has_critical,
                   total_ms=int((time.perf_counter() - t0) * 1000))
        return {
            "task_id": t.task_id,
            "status": final_status,
            "summary": run.summary,
            "degradation": {
                "level": ledger.level(),
                "count": ledger.count,
                "has_critical": ledger.has_critical,
            },
        }

    except RunCancelled:
        run.status = "canceled"
        run.finished_at = dt.datetime.utcnow()
        db.commit()
        # 运行透视 落盘 — cancelled path: mark the in-flight stage skipped so the
        # pipeline shows where the run stopped (④⑤逐日表 absent — compute halted).
        if trace:
            rec = trace.stage_record(stage) or trace.stage(stage)
            if rec.status not in ("blocked", "error"):
                trace.finish_stage(rec, status="skipped")
            _write_trace_safe(run_id, trace)
        log_write("CANCELLED")
        _qlog.warn("run.canceled", run_id=run_id, stage=stage,
                   total_ms=int((time.perf_counter() - t0) * 1000))
        return {"task_id": t.task_id, "status": "canceled"}
    except Exception as e:  # noqa: BLE001
        run.status = "failed"
        run.error = f"{type(e).__name__}: {e}"
        run.finished_at = dt.datetime.utcnow()
        db.commit()
        # 运行透视 落盘 — failed path: stamp the failing stage with the error so the
        # pipeline highlights it (设计 §2.3f: all terminal paths落盘).
        if trace:
            rec = trace.stage_record(stage) or trace.stage(stage)
            trace.finish_stage(rec, status="error", error=f"{type(e).__name__}: {e}")
            _write_trace_safe(run_id, trace)
        log_write(f"FAILED · {run.error}")
        # ERROR auto-attaches the stack; `stage` pinpoints which phase blew up.
        _qlog.error("run.failed", exc=e, run_id=run_id, stage=stage)
        raise
    finally:
        # 资源画像收尾 — best-effort, runs on every terminal path (success / cancel
        # / fail / preflight-blocked). Embeds the `resources` node into trace.json.
        _finalize_resources(
            db, collector, profiler, run, dict(run.run_config or {}), "evaluation", trace
        )
        if log_file:
            log_file.close()


def _write_trace_safe(run_id: str, trace) -> None:
    """Persist the 运行透视 artifact — never let a trace-write failure sink a run.

    设计 §2.3f / §5: writing the trace is best-effort. Any error is logged at
    WARNING and swallowed; the run's own status is决定 elsewhere.
    """
    if trace is None:
        return
    try:
        write_run_trace(run_id, trace.to_jsonable())
    except Exception:  # noqa: BLE001
        _log.warning("write run trace failed (non-fatal)", exc_info=True)


# ─── 资源画像收尾 (资源画像方案 §3.3/§3.4/§3.5) ───────────────────────────────
def _funnel_final_symbols(trace) -> int | None:
    """Pull the universe funnel's final symbol count for workload归一化 (方案 §1)."""
    if trace is None:
        return None
    try:
        rec = trace.stage_record("universe_resolve")
        funnel = (rec.detail.get("funnel") if rec and rec.detail else None) or []
        if funnel:
            return funnel[-1].get("symbols")
    except Exception:  # noqa: BLE001
        return None
    return None


def _finalize_resources(db, collector, profiler, run, run_config, task_type, trace) -> None:
    """Slice samples → resources节 + summary/baseline/artifacts, merge into trace.

    Best-effort on every terminal path; any failure is logged and swallowed so a
    采集 hiccup never changes the run's outcome (方案 §3.1 失败安全红线)。
    """
    if collector is None and profiler is None:
        return
    try:
        if profiler is not None:
            profiler.stop()
        profiled = bool(profiler is not None and profiler.has_samples())
        resources = None
        if collector is not None:
            admission = REGISTRY.get_admission(collector.task_id)
            resources = resource_writer.finalize_run_resources(
                db, collector, run_config=run_config, task_type=task_type,
                profiled=profiled, n_symbols=_funnel_final_symbols(trace),
                admission=admission,
            )
        if profiled:
            try:
                resource_writer.write_gz_artifact(
                    run.run_id, "profile", profiler.to_artifact()
                )
            except Exception:  # noqa: BLE001
                _log.warning("profile artifact write failed (non-fatal)", exc_info=True)
                if resources is not None:
                    resources["has_profile"] = False
        if resources is not None:
            resource_writer.merge_resources_into_trace(
                run.run_id, resources,
                fallback_payload=trace.to_jsonable() if trace is not None else None,
            )
    except Exception:  # noqa: BLE001
        _log.warning("resource finalize failed (non-fatal)", exc_info=True)
    # NB: the registry entry is cleared by the dispatcher on finalize (symmetric
    # with mark_inflight) — keeping it here keeps this task counted as in-flight
    # for concurrent runs' attribution until the future truly completes.


def _resolve_symbols_for_preflight(reader, uni_def, custom_syms, cfg) -> list[str]:
    """Resolve the window's symbol set for data-coverage checks (设计 §7).

    Reuses ``resolve_universe`` (no ledger — the engine re-resolves with the
    ledger so filter no-ops are recorded once, at runtime). Best-effort: on any
    resolution error we fall back to the custom list so structural checks
    (window / preprocess / flag) still run.
    """
    try:
        uni = resolve_universe(reader, uni_def, custom_syms, cfg.start, cfg.end)
        if uni.height == 0:
            return list(custom_syms or [])
        return sorted(uni["symbol"].unique().to_list())
    except Exception:  # noqa: BLE001
        return list(custom_syms or [])


def _persist_degradation(db, run_id: str, ledger) -> None:
    """Insert one factor_run_degradation row per台账 event (设计 §8.2)."""
    for ev in ledger.events:
        db.add(
            FactorRunDegradation(
                run_id=run_id,
                code=ev.code,
                category=ev.category,
                severity=ev.severity,
                stage=ev.stage,
                config_key=ev.config_key,
                title=ev.title,
                reason=ev.reason,
                impact=ev.impact,
                suggested_action=ev.suggested_action,
                policy=ev.policy,
                evidence_json=ev.evidence,
                occurred_at=ev.occurred_at,
            )
        )


def _finish_preflight_blocked(db, run, run_id, t, pb, log_write, t0) -> dict[str, Any]:
    """Terminal handling of a preflight block (设计 §7): status=failed, structured
    阻断详情 persisted, artifact written, NO compute, NO retry."""
    blocked_json = [check_result_to_json(r) for r in pb.results]
    has_critical = any(b.get("severity") == "critical" for b in blocked_json)
    run.status = "failed"
    run.error = "preflight_blocked"
    run.preflight = blocked_json
    run.degradation_level = "severe" if has_critical else "partial"
    run.degradation_count = len(blocked_json)
    run.has_critical = has_critical
    run.finished_at = dt.datetime.utcnow()
    if run.started_at:
        run.duration_sec = (run.finished_at - run.started_at).total_seconds()
    # Persist the blocked checks as degradation rows (policy=block) for stats.
    for r in pb.results:
        ev = r.event
        if ev is None:
            continue
        db.add(
            FactorRunDegradation(
                run_id=run_id, code=ev.code, category=ev.category,
                severity=ev.severity, stage=ev.stage, config_key=ev.config_key,
                title=ev.title, reason=ev.reason, impact=ev.impact,
                suggested_action=ev.suggested_action, policy="block",
                evidence_json=ev.evidence, occurred_at=ev.occurred_at,
            )
        )
    db.commit()
    write_degradations(
        run_id, status="failed", level=run.degradation_level,
        blocked=blocked_json, events=[],
    )
    log_write(f"PREFLIGHT BLOCKED · {[r.check_id for r in pb.results]}")
    _qlog.warn(
        "run.preflight.blocked", run_id=run_id,
        checks=[r.check_id for r in pb.results],
        total_ms=int((time.perf_counter() - t0) * 1000),
    )
    return {
        "task_id": t.task_id,
        "status": "failed",
        "reason": "preflight_blocked",
        "blocked": [r.check_id for r in pb.results],
    }


def _persist_eval_summary(
    db, run: FactorRun, cfg: EvalConfig, uni_id: str, result: dict, ledger=None
) -> None:
    s = result.get("summary") or {}
    # Compute a small IC spark (last 20 rank_ic values) for the library table.
    # NB: must be an explicit `is None` check — `df or default` evaluates
    # bool(df), which a non-empty polars DataFrame refuses ("truth value is
    # ambiguous"), so the old `or` form crashed every successful eval that
    # produced a real ic_series (only latent because demo data yielded none).
    ic_df = result.get("ic_series")
    if ic_df is None:
        ic_df = pl.DataFrame()
    spark: list[float] = []
    if ic_df.height > 0 and "rank_ic" in ic_df.columns:
        spark = [
            float(x) if x is not None else 0.0
            for x in ic_df.tail(20)["rank_ic"].to_list()
        ]
    es = EvalSummary(
        run_id=run.run_id,
        factor_id=run.factor_id,
        as_of=dt.datetime.utcnow(),
        universe_id=uni_id,
        primary_horizon=int(cfg.primary_horizon),
        rebalance=str(cfg.rebalance),
        ic_mean=float(s.get("ic_mean", 0.0)),
        rank_ic_mean=float(s.get("rank_ic_mean", 0.0)),
        icir=float(s.get("icir", 0.0)),
        icir_annual=float(s.get("icir_annual", 0.0)),
        ic_t_stat=float(s.get("ic_t_stat", 0.0)),
        ic_win_rate=float(s.get("ic_win_rate", 0.0)),
        ls_annual_return=float(s.get("ls_annual_return", 0.0)),
        ls_sharpe=float(s.get("ls_sharpe", 0.0)),
        ls_max_drawdown=float(s.get("ls_max_drawdown", 0.0)),
        long_annual_return=float(s.get("long_annual_return", 0.0)),
        short_annual_return=float(s.get("short_annual_return", 0.0)),
        turnover=float(s.get("turnover", 0.0)),
        turnover_after_cost_ann=float(s.get("turnover_after_cost_ann", 0.0)),
        coverage=float(s.get("coverage", 0.0)),
        monotonic=bool(s.get("monotonic", False)),
        ic_spark=spark,
        # Denormalised degradation snapshot for the list pages (设计 §8.1).
        degradation_level=ledger.level() if ledger is not None else "none",
        degradation_count=ledger.count if ledger is not None else 0,
        has_critical=ledger.has_critical if ledger is not None else False,
    )
    db.add(es)
    db.commit()


def _universe_row_to_dict(u: Universe) -> dict:
    return {
        "universe_id": u.universe_id,
        "kind": u.kind,
        "preset_flag": u.preset_flag,
        "classification_system": u.classification_system,
        "default_industry_level": u.default_industry_level,
        "exclude_st": u.exclude_st,
        "exclude_suspended": u.exclude_suspended,
        "min_list_days": u.min_list_days,
        "snapshot_retention_days": u.snapshot_retention_days,
    }


# ─────────────────────────────────────────────────────────────────────────────
def _run_correlation(db, t: Task) -> dict[str, Any]:
    payload = t.payload or {}
    fids: list[str] = list(payload.get("factor_ids") or [])
    if len(fids) < 2:
        raise ValueError("correlation needs at least 2 factor_ids")

    cfg = EvalConfig.from_dict(payload.get("run_config") or {})
    uni_row = db.get(Universe, cfg.universe_id)
    if uni_row is None:
        raise ValueError(f"universe {cfg.universe_id} not found")
    uni_def = _universe_row_to_dict(uni_row)
    custom = None
    if uni_row.kind == "custom":
        from ..db.models import UniverseMember

        custom = [
            r[0]
            for r in db.query(UniverseMember.symbol)
            .filter(UniverseMember.universe_id == uni_row.universe_id)
            .all()
        ]

    # Build instances using each factor's enabled version's class.
    instances = {}
    icir_lookup = {}
    for fid in fids:
        rec = db.get(FactorRecord, fid)
        if rec is None:
            raise ValueError(f"factor {fid} not found")
        # Most-recent enabled version:
        ver = (
            db.query(FactorVersion)
            .filter(FactorVersion.factor_id == fid, FactorVersion.enabled == True)  # noqa: E712
            .order_by(FactorVersion.id.desc())
            .first()
        )
        if ver is None:
            raise ValueError(f"factor {fid} has no enabled version")
        if ver.code_path and os.path.exists(ver.code_path):
            with open(ver.code_path, encoding="utf-8") as fh:
                instances[fid] = instantiate_from_code(fh.read(), ver.class_name)
        else:
            instances[fid] = instantiate(rec.base_name)
        last = (
            db.query(EvalSummary)
            .filter(EvalSummary.factor_id == fid)
            .order_by(EvalSummary.id.desc())
            .first()
        )
        icir_lookup[fid] = float(last.icir) if last else 0.0

    def progress(c, n):
        store.heartbeat(db, t.task_id, current=int(c), total=int(n))

    def cancelled():
        db.refresh(t)
        return bool(t.cancel_requested)

    reader = DataReader(root=get_settings().quant_data_root)
    _qlog.info("correlation.start", task_id=t.task_id, n_factors=len(fids),
               method=str(payload.get("method", "ic_series")))
    try:
        with qd_logging.timed(_qlog, "correlation.compute", task_id=t.task_id) as m:
            result = correlation_matrix(
                reader,
                instances,
                cfg,
                uni_def,
                custom_symbols=custom,
                method=str(payload.get("method", "ic_series")),
                threshold=float(payload.get("threshold", 0.7)),
                icir_lookup=icir_lookup,
                progress_cb=progress,
                cancel_cb=cancelled,
            )
            write_correlation_cache(t.task_id, result)
            m["n_factors"] = len(fids)
            m["matrix_size"] = len(fids) * len(fids)
    except RunCancelled:
        # 用户主动取消:终态=canceled,绝不当作 failed 触发自动重试
        # (与 _run_evaluation / run_basket_eval 的取消处理对齐)。
        _qlog.warn("correlation.canceled", task_id=t.task_id, n_factors=len(fids))
        return {"task_id": t.task_id, "status": "canceled"}
    except Exception as e:  # noqa: BLE001
        _qlog.error("correlation.failed", exc=e, task_id=t.task_id, n_factors=len(fids))
        raise
    return {"task_id": t.task_id, "status": "success", "summary": result["summary"]}


# ─────────────────────────────────────────────────────────────────────────────
def _run_import_validate(db, t: Task) -> dict[str, Any]:
    from quant_factor.validate import validate_factor

    src = t.payload.get("source") or ""
    class_hint = t.payload.get("class_name") or None
    deps = t.payload.get("dependencies") or []
    reader = DataReader(root=get_settings().quant_data_root)
    rep = validate_factor(
        src,
        reader=reader,
        class_name_hint=class_hint,
        declared_dependencies=deps,
    )
    return {"task_id": t.task_id, "status": "success", "summary": rep["summary"]}
