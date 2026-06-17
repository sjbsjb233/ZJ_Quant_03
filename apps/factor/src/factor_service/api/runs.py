"""Run lifecycle — submit / list / get / cancel / retry / log SSE."""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import uuid
from typing import Any, AsyncGenerator

import anyio
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from quant_data import logging as qd_logging
from quant_factor.config import EvalConfig

from ..config import get_settings
from ..db.models import (
    FactorRecord,
    FactorRun,
    FactorRunDegradation,
    FactorVersion,
    RunResourceStat,
    Task,
)
from ..deps import current_user, get_db, require_admin, require_editor
from ..errors import ApiError
from ..logging_ctx import trace_id_var
from ..queue import store
from ..services.preflight_service import dry_run_preflight


router = APIRouter()

# Structured logger (source="factor", carries the request trace_id). The matching
# worker-side chain (run.start → run.stage.* → run.done) is in run_executor; both
# share the same trace_id so a run reads end-to-end in app_log (V3 §4.3).
_qlog = qd_logging.get_logger("factor.runs")


@router.post("/runs")
def submit_run(
    body: dict[str, Any],
    db: Session = Depends(get_db),
    user=Depends(require_editor),
) -> dict[str, Any]:
    """Submit an evaluation run -> enqueues an `evaluation` task."""
    factor_id = body.get("factor_id")
    if not factor_id:
        raise ApiError("INVALID_PARAMETER", "factor_id 是必填项")
    rec = db.get(FactorRecord, factor_id)
    if rec is None:
        raise ApiError("FACTOR_NOT_FOUND", f"因子 {factor_id} 不存在")

    version = body.get("version")
    if not version:
        ver = (
            db.query(FactorVersion)
            .filter(FactorVersion.factor_id == factor_id, FactorVersion.enabled == True)  # noqa: E712
            .order_by(FactorVersion.id.desc())
            .first()
        )
        if ver is None:
            raise ApiError(
                "PRECONDITION_FAILED", f"因子 {factor_id} 无 enabled 版本"
            )
        version = ver.version
    else:
        ver = (
            db.query(FactorVersion)
            .filter(
                FactorVersion.factor_id == factor_id, FactorVersion.version == version
            )
            .first()
        )
        if ver is None:
            raise ApiError(
                "VERSION_NOT_FOUND", f"版本 {factor_id}@{version} 不存在"
            )
        if not ver.enabled:
            raise ApiError(
                "PRECONDITION_FAILED",
                f"版本 {factor_id}@{version} 尚未启用,请先审核启用",
            )

    run_config = body.get("run_config") or {}
    # Sanity check the config — turn ValueError into a clean 400.
    try:
        cfg = EvalConfig.from_dict(run_config)
    except (ValueError, TypeError) as e:
        raise ApiError("INVALID_PARAMETER", f"run_config 无效: {e}")
    if not (cfg.start and cfg.end):
        # Default to last year
        cfg.end = dt.date.today()
        cfg.start = cfg.end - dt.timedelta(days=365)
    if cfg.start >= cfg.end:
        raise ApiError("INVALID_PARAMETER", "start 必须早于 end")

    params = body.get("params") or {}
    # 资源画像剖析开关 (方案 §6): 管理员可提交剖析运行 (默认关闭, 有开销与体积成本).
    profile = bool(body.get("profile")) and user.role == "admin"

    # ── preflight gate at the source (设计 §10.3) ────────────────────────────
    # Refuse to create a run that would be blocked — return 422 with the
    # structured would_block so the UI can guide a fix. Preflight failures (e.g.
    # data-layer hiccup) must NOT block submission: the worker's preflight stage
    # is the authoritative backstop, so we proceed on error.
    s = get_settings()
    if s.preflight_enabled:
        try:
            pf = dry_run_preflight(
                db, factor_id=factor_id, version=version, run_config=run_config
            )
        except ValueError:
            pf = None
        except Exception as e:  # noqa: BLE001
            _qlog.warn("run.preflight.dryrun_error", factor_id=factor_id, err=str(e))
            pf = None
        if pf and pf.get("would_block"):
            _qlog.warn(
                "run.submit.blocked", factor_id=factor_id,
                checks=[b["check_id"] for b in pf["would_block"]],
            )
            raise ApiError(
                "PREFLIGHT_BLOCKED",
                "运行前置校验未通过,已阻断:请补齐数据或调整配置后重试",
                details={
                    "would_block": pf["would_block"],
                    "would_degrade": pf.get("would_degrade", []),
                },
                status_code=422,
            )

    run_id = f"frun_{uuid.uuid4().hex[:16]}"
    run = FactorRun(
        run_id=run_id,
        factor_id=factor_id,
        version=version,
        params=params,
        run_config=cfg.to_jsonable(),
        status="queued",
        initiated_by=user.username,
        trace_id=trace_id_var.get(),
    )
    db.add(run)
    db.commit()

    lane = (run_config.get("lane") or cfg.lane or "interactive")
    # 入队失败时,已落库的 run 须置为 failed,否则会永远卡在 queued (无 task 兜底)。
    try:
        task = store.enqueue(
            db,
            task_type="evaluation",
            payload={
                "run_id": run_id,
                "factor_id": factor_id,
                "version": version,
                "params": params,
                "run_config": cfg.to_jsonable(),
                "profile": profile,
            },
            lane=lane,
            run_id=run_id,
            factor_id=factor_id,
            initiated_by=user.username,
            trace_id=trace_id_var.get(),
        )
    except Exception as e:  # noqa: BLE001
        db.rollback()
        run.status = "failed"
        run.error = f"任务入队失败: {e}"
        db.commit()
        _qlog.warn("run.submit.enqueue_failed", run_id=run_id, factor_id=factor_id,
                   err=str(e))
        # 不把内部异常串回传客户端(与 errors.py 不泄漏内部细节一致);详情已落日志 + run.error。
        raise ApiError("INTERNAL", "任务入队失败")
    run.task_id = task.task_id
    db.commit()
    _qlog.info("run.submit", run_id=run_id, factor_id=factor_id, version=version,
               task_id=task.task_id, universe=cfg.universe_id, lane=task.lane,
               profile=profile, **{"from": str(cfg.start), "to": str(cfg.end)})
    return {
        "run_id": run_id,
        "task_id": task.task_id,
        "status": "queued",
        "lane": task.lane,
        "profile": profile,
        "est_duration_sec": int(task.est_duration_sec),
    }


@router.post("/runs/preview-eta")
def preview_eta(
    body: dict[str, Any],
    db: Session = Depends(get_db),
    user=Depends(current_user),
) -> dict[str, Any]:
    """Estimate duration + queue position BEFORE submitting (no DB write)."""
    from ..queue.estimator import estimate_duration

    task_type = body.get("task_type", "evaluation")
    payload = body.get("payload") or {}
    est = estimate_duration(db, task_type, payload)
    lane = body.get("lane", "interactive")
    priority = 100 if lane == "interactive" else 10
    # How many queued tasks in this lane with >= priority?
    ahead = (
        db.query(Task)
        .filter(
            Task.status == "queued",
            Task.lane == lane,
            Task.priority >= priority,
        )
        .count()
    )
    return {
        "est_duration_sec": int(est),
        "lane": lane,
        "queue_position": ahead + 1,
    }


@router.post("/runs/preflight")
def preflight_dry_run(
    body: dict[str, Any],
    db: Session = Depends(get_db),
    user=Depends(current_user),
) -> dict[str, Any]:
    """Dry-run the preflight gate BEFORE submitting (设计 §10.2).

    Body: {factor_id, version?, run_config}. Returns
    {would_block, would_degrade, runnable} so the UI can disable the run button
    (block) or pop a confirm (degrade) without ever queuing a doomed task.
    """
    factor_id = body.get("factor_id")
    if not factor_id:
        raise ApiError("INVALID_PARAMETER", "factor_id 是必填项")
    try:
        return dry_run_preflight(
            db,
            factor_id=factor_id,
            version=body.get("version"),
            run_config=body.get("run_config") or {},
        )
    except ValueError as e:
        raise ApiError("INVALID_PARAMETER", str(e))


@router.get("/runs/resource-budgets")
def resource_budget_advice(
    db: Session = Depends(get_db),
    user=Depends(require_admin),
) -> dict[str, Any]:
    """预算重标定建议 (资源画像方案 §8) — 任务类实测 p90_peak_mb vs 准入预算.

    只建议不自动改 (预算静态配置的哲学不变, §8)。``p90 > 0.9×预算`` → 建议调升;
    ``p90 < 0.5×预算`` → 建议调降。数据源 = 同类基线 (run_resource_stat) 的滚动
    p90;预算源 = quant_admission 预算表 (治理方案 WP5 落地后即对齐)。
    """
    import json
    from quant_admission import DEFAULT_BUDGETS

    budgets = dict(DEFAULT_BUDGETS)
    raw = os.environ.get("ADMISSION_BUDGETS_JSON")
    if raw:
        try:
            budgets.update({str(k): float(v) for k, v in json.loads(raw).items()})
        except (ValueError, TypeError, AttributeError):
            pass

    items = []
    for cls, budget in sorted(budgets.items()):
        st = db.get(RunResourceStat, f"{cls}#exact")
        if st is None or st.sample_n == 0 or not budget:
            continue
        p90 = st.p90_peak_mb
        ratio = p90 / budget if budget else 0.0
        if ratio > 0.9:
            advice = "raise"
        elif ratio < 0.5:
            advice = "lower"
        else:
            advice = "ok"
        items.append({
            "task_class": cls, "budget_mb": round(budget, 0),
            "p90_peak_mb": round(p90, 0), "ratio": round(ratio, 2),
            "sample_n": st.sample_n, "advice": advice,
        })
    return {"items": items, "note": "只建议不自动改 — 预算为静态配置 (资源画像方案 §8)"}


@router.get("/runs")
def list_runs(
    factor_id: str | None = None,
    status: str | None = None,
    lane: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    user=Depends(current_user),
) -> dict:
    q = db.query(FactorRun)
    if factor_id:
        q = q.filter(FactorRun.factor_id == factor_id)
    if status:
        q = q.filter(FactorRun.status == status)
    rows = q.order_by(FactorRun.created_at.desc()).limit(limit).all()
    return {"items": [_serialize_run(r) for r in rows]}


@router.get("/runs/{run_id}")
def get_run(
    run_id: str, db: Session = Depends(get_db), user=Depends(current_user)
) -> dict[str, Any]:
    r = db.get(FactorRun, run_id)
    if r is None:
        raise ApiError("RUN_NOT_FOUND", f"运行 {run_id} 不存在")
    payload = _serialize_run(r)
    payload["degradation"] = _degradation_payload(db, r)
    # Live progress from the task row
    if r.task_id:
        t = db.get(Task, r.task_id)
        if t is not None:
            payload["task"] = {
                "task_id": t.task_id,
                "status": t.status,
                "lane": t.lane,
                "progress_current": t.progress_current,
                "progress_total": t.progress_total,
                "est_duration_sec": t.est_duration_sec,
                "attempt": t.attempt,
                "heartbeat_at": t.heartbeat_at.isoformat() + "Z"
                if t.heartbeat_at
                else None,
                "error": t.error,
            }
    return payload


def _serialize_run(r: FactorRun) -> dict[str, Any]:
    return {
        "run_id": r.run_id,
        "factor_id": r.factor_id,
        "version": r.version,
        "params": dict(r.params or {}),
        "run_config": dict(r.run_config or {}),
        "status": r.status,
        "task_id": r.task_id,
        "initiated_by": r.initiated_by,
        "trace_id": r.trace_id,
        "error": r.error,
        "summary": dict(r.summary or {}),
        # ── degradation derived fields (设计 §3 / §10.1) ──────────────────
        "degradation_level": getattr(r, "degradation_level", "none") or "none",
        "degradation_count": getattr(r, "degradation_count", 0) or 0,
        "has_critical": bool(getattr(r, "has_critical", False)),
        "preflight": getattr(r, "preflight", None),
        "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
        "started_at": r.started_at.isoformat() + "Z" if r.started_at else None,
        "finished_at": r.finished_at.isoformat() + "Z" if r.finished_at else None,
        "duration_sec": r.duration_sec,
    }


def _degradation_payload(db: Session, r: FactorRun) -> dict[str, Any]:
    """Assemble the run's degradation block (设计 §10.1) from the DB rows."""
    rows = (
        db.query(FactorRunDegradation)
        .filter(FactorRunDegradation.run_id == r.run_id)
        .all()
    )
    events = [
        {
            "code": d.code,
            "category": d.category,
            "severity": d.severity,
            "stage": d.stage,
            "config_key": d.config_key,
            "title": d.title,
            "reason": d.reason,
            "impact": d.impact,
            "suggested_action": d.suggested_action,
            "policy": d.policy,
            "evidence": dict(d.evidence_json or {}),
            "occurred_at": d.occurred_at,
        }
        for d in rows
        if d.policy != "block"  # block rows surface via `preflight`, not events
    ]
    return {
        "level": getattr(r, "degradation_level", "none") or "none",
        "count": getattr(r, "degradation_count", 0) or 0,
        "has_critical": bool(getattr(r, "has_critical", False)),
        "events": events,
    }


@router.post("/runs/{run_id}/cancel")
def cancel_run(
    run_id: str,
    db: Session = Depends(get_db),
    user=Depends(require_editor),
) -> dict:
    r = db.get(FactorRun, run_id)
    if r is None:
        raise ApiError("RUN_NOT_FOUND", f"运行 {run_id} 不存在")
    if r.task_id:
        store.request_cancel(db, r.task_id)
    if r.status == "queued":
        r.status = "canceled"
        db.commit()
    return {"run_id": run_id, "status": r.status}


@router.post("/runs/{run_id}/retry")
def retry_run(
    run_id: str,
    body: dict[str, Any] | None = None,
    db: Session = Depends(get_db),
    user=Depends(require_editor),
) -> dict[str, Any]:
    r = db.get(FactorRun, run_id)
    if r is None:
        raise ApiError("RUN_NOT_FOUND", f"运行 {run_id} 不存在")
    # 「以剖析模式重跑」(资源画像方案 §6): 复用重跑端点 + profile 参数 (管理员).
    profile = bool((body or {}).get("profile")) and user.role == "admin"
    # New run + new task with original config.
    new_id = f"frun_{uuid.uuid4().hex[:16]}"
    nr = FactorRun(
        run_id=new_id,
        factor_id=r.factor_id,
        version=r.version,
        params=dict(r.params or {}),
        run_config=dict(r.run_config or {}),
        status="queued",
        initiated_by=user.username,
        trace_id=trace_id_var.get(),
    )
    db.add(nr)
    db.commit()
    # 入队失败时,已落库的新 run 须置为 failed,否则会永远卡在 queued (无 task 兜底)。
    try:
        task = store.enqueue(
            db,
            task_type="evaluation",
            payload={
                "run_id": new_id,
                "factor_id": r.factor_id,
                "version": r.version,
                "params": dict(r.params or {}),
                "run_config": dict(r.run_config or {}),
                "profile": profile,
            },
            lane=(r.run_config or {}).get("lane") or "interactive",
            run_id=new_id,
            factor_id=r.factor_id,
            initiated_by=user.username,
            trace_id=trace_id_var.get(),
        )
    except Exception as e:  # noqa: BLE001
        db.rollback()
        nr.status = "failed"
        nr.error = f"任务入队失败: {e}"
        db.commit()
        _qlog.warn("run.submit.enqueue_failed", run_id=new_id, factor_id=r.factor_id,
                   retried_from=run_id, err=str(e))
        raise ApiError("INTERNAL", f"任务入队失败: {e}")
    nr.task_id = task.task_id
    db.commit()
    _qlog.info("run.submit", run_id=new_id, factor_id=r.factor_id, version=r.version,
               task_id=task.task_id, retried_from=run_id, profile=profile)
    return {"run_id": new_id, "task_id": task.task_id, "status": "queued", "profile": profile}


# ─────────────────────────────────────────────────────────────────────────────
@router.get("/runs/{run_id}/logs/stream")
async def stream_logs(
    run_id: str,
    db: Session = Depends(get_db),
    user=Depends(current_user),
) -> StreamingResponse:
    """Server-sent events: tail the runs/<run_id>.log file."""
    # 鉴权 + 运行存在性校验:本端点须与同级端点一致要求登录,
    # 且不可对不存在的运行打开日志文件 (避免无鉴权读取日志)。
    if db.get(FactorRun, run_id) is None:
        raise ApiError("RUN_NOT_FOUND", f"运行 {run_id} 不存在")
    s = get_settings()
    path = os.path.join(s.runs_dir, f"{run_id}.log")

    def _read_chunk(pos: int) -> tuple[bytes, int]:
        # 同步阻塞文件读 — 放到工作线程执行 (见 gen),避免阻塞事件循环。
        if not os.path.exists(path):
            return b"", pos
        with open(path, "rb") as f:
            f.seek(pos)
            chunk = f.read()
            return chunk, f.tell()

    async def gen() -> AsyncGenerator[bytes, None]:
        yield b": connected\n\n"
        pos = 0
        for _ in range(2400):  # max ~2400 * 0.5s = 20 minutes
            # 文件 I/O 卸载到线程池,SSE 扇出时不阻塞 asyncio 事件循环。
            chunk, pos = await anyio.to_thread.run_sync(_read_chunk, pos)
            if chunk:
                for line in chunk.splitlines():
                    if not line:
                        continue
                    yield b"data: " + line + b"\n\n"
            await asyncio.sleep(0.5)
        yield b"event: done\ndata: end-of-stream\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
