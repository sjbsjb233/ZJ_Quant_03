"""Correlation analysis — submit + read."""
from __future__ import annotations

import io
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from quant_data import DataReader, logging as qd_logging
from quant_factor.cache import data_fingerprint
from quant_factor.config import EvalConfig, config_signature

from ..config import get_settings
from ..db.models import EvalSummary, FactorRecord, Task
from ..deps import current_user, get_db, require_editor
from ..errors import ApiError
from ..logging_ctx import trace_id_var
from ..queue import store
from ..services import results_writer


router = APIRouter()

# The matching compute chain (correlation.compute.done) is in run_executor; same
# trace_id stitches submit → compute in app_log (V3 §4.3).
_qlog = qd_logging.get_logger("factor.correlation")


def _severe_factors(db: Session, fids: list[str]) -> list[str]:
    """Factor ids whose *latest* evaluation was severe-degraded (设计 §9.2)."""
    out: list[str] = []
    for fid in fids:
        latest = (
            db.query(EvalSummary)
            .filter(EvalSummary.factor_id == fid)
            .order_by(EvalSummary.id.desc())
            .first()
        )
        if latest is not None and (
            bool(getattr(latest, "has_critical", False))
            or getattr(latest, "degradation_level", "none") == "severe"
        ):
            out.append(fid)
    return out


@router.post("/factors/correlation")
def submit_correlation(
    body: dict[str, Any],
    db: Session = Depends(get_db),
    user=Depends(require_editor),
) -> dict[str, Any]:
    """Submit a correlation analysis — runs as a `correlation` queue task."""
    fids = list(body.get("factor_ids") or [])
    if len(fids) < 2:
        raise ApiError("INVALID_PARAMETER", "factor_ids 至少需要 2 个")
    # Validate all exist
    for fid in fids:
        if db.get(FactorRecord, fid) is None:
            raise ApiError("FACTOR_NOT_FOUND", f"因子 {fid} 不存在")

    # severe-degraded results must not silently feed a downstream analysis
    # (设计 §9.2 / §11): a factor whose latest evaluation carried a critical
    # degradation is refused unless the caller explicitly acknowledges the risk.
    if not body.get("acknowledge_degraded"):
        severe = _severe_factors(db, fids)
        if severe:
            _qlog.warn("correlation.severe_blocked", factor_ids=severe)
            raise ApiError(
                "DEGRADED_INPUT_BLOCKED",
                "存在严重降级(severe)的因子,默认不参与相关性分析;"
                "确认风险后可携带 acknowledge_degraded=true 重试",
                details={"severe_factors": severe},
                status_code=422,
            )

    cfg = EvalConfig.from_dict(body.get("run_config") or {})
    method = body.get("method") or "ic_series"
    threshold = float(body.get("threshold") or 0.7)

    # Fold a data-availability fingerprint into the signature (设计 §9.1) so that
    # backfilling sectors / circ_mv / vwap changes the key — a degraded result is
    # never reused once the missing data is补齐.
    try:
        fp = data_fingerprint(DataReader(root=get_settings().quant_data_root), cfg)
    except Exception:  # noqa: BLE001
        fp = "fp:err"
    sig = config_signature(
        cfg, extra=(method, str(threshold), ",".join(sorted(fids)), fp)
    )

    task = store.enqueue(
        db,
        task_type="correlation",
        payload={
            "factor_ids": fids,
            "run_config": cfg.to_jsonable(),
            "method": method,
            "threshold": threshold,
            "signature": sig,
        },
        lane=body.get("lane") or "interactive",
        initiated_by=user.username,
        trace_id=trace_id_var.get(),
    )
    _qlog.info("correlation.submit", task_id=task.task_id, n_factors=len(fids),
               method=method, threshold=threshold, lane=task.lane)
    return {
        "task_id": task.task_id,
        "status": "queued",
        "signature": sig,
        "est_duration_sec": int(task.est_duration_sec),
    }


@router.get("/factors/correlation/{task_id}")
def get_correlation(
    task_id: str,
    format: str | None = Query(None),
    db: Session = Depends(get_db),
    user=Depends(current_user),
):
    t = db.get(Task, task_id)
    if t is None and not results_writer.read_correlation(task_id):
        raise ApiError("TASK_NOT_FOUND", f"任务 {task_id} 不存在")
    cached = results_writer.read_correlation(task_id)
    if cached is None:
        if t is not None and t.status in ("queued", "running"):
            return {
                "task_id": task_id,
                "status": t.status,
                "progress_current": t.progress_current,
                "progress_total": t.progress_total,
            }
        raise ApiError("PRECONDITION_FAILED", "结果尚未就绪")
    if format == "csv":
        labels = cached.get("labels", [])
        mat = cached.get("matrix", [])
        buf = io.StringIO()
        buf.write("," + ",".join(labels) + "\n")
        for i, row in enumerate(mat):
            buf.write(
                labels[i] + "," + ",".join(f"{v:.3f}" for v in row) + "\n"
            )
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="corr_{task_id}.csv"'
            },
        )
    return {"task_id": task_id, "status": "success", **cached}
