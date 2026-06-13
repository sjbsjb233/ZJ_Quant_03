"""Result-reading endpoints — IC series / layered / decay / turnover."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db.models import EvalSummary, FactorRun
from ..deps import current_user, get_db
from ..errors import ApiError
from ..services import resource_writer, results_writer


router = APIRouter()


def _latest_run_id(db: Session, factor_id: str) -> str | None:
    es = (
        db.query(EvalSummary)
        .filter(EvalSummary.factor_id == factor_id)
        .order_by(EvalSummary.id.desc())
        .first()
    )
    return es.run_id if es else None


def _factor_of_run(db: Session, run_id: str) -> str:
    r = db.get(FactorRun, run_id)
    if r is None:
        raise ApiError("RUN_NOT_FOUND", f"运行 {run_id} 不存在")
    return r.factor_id


@router.get("/runs/{run_id}/ic-series")
def ic_series(
    run_id: str, db: Session = Depends(get_db), user=Depends(current_user)
) -> dict:
    fid = _factor_of_run(db, run_id)
    return {
        "run_id": run_id,
        "factor_id": fid,
        "items": results_writer.read_ic_series(fid, run_id),
    }


@router.get("/runs/{run_id}/layers")
def layers(
    run_id: str, db: Session = Depends(get_db), user=Depends(current_user)
) -> dict:
    fid = _factor_of_run(db, run_id)
    return {
        "run_id": run_id,
        "factor_id": fid,
        "items": results_writer.read_layers(fid, run_id),
        "decay": results_writer.read_decay(fid, run_id),
    }


@router.get("/runs/{run_id}/turnover")
def turnover(
    run_id: str, db: Session = Depends(get_db), user=Depends(current_user)
) -> dict:
    fid = _factor_of_run(db, run_id)
    return {
        "run_id": run_id,
        "factor_id": fid,
        "data": results_writer.read_turnover(fid, run_id),
    }


@router.get("/runs/{run_id}/degradations")
def degradations(
    run_id: str, db: Session = Depends(get_db), user=Depends(current_user)
) -> dict:
    """Full structured degradation台账 + 阻断详情 (设计 §8.3 artifact)."""
    r = db.get(FactorRun, run_id)
    if r is None:
        raise ApiError("RUN_NOT_FOUND", f"运行 {run_id} 不存在")
    art = results_writer.read_degradations(run_id)
    if art is not None:
        return art
    # Fallback (artifact missing): synthesize from the persisted columns.
    return {
        "run_id": run_id,
        "status": r.status,
        "level": getattr(r, "degradation_level", "none") or "none",
        "blocked": getattr(r, "preflight", None) or [],
        "events": [],
    }


@router.get("/runs/{run_id}/trace")
def run_trace(
    run_id: str, db: Session = Depends(get_db), user=Depends(current_user)
) -> dict:
    """运行透视数据 (设计 §2.5): 阶段流水线 + 前置检查全量 + 股票池漏斗 + 逐调仓日过程.

    web-backend's ``factors_proxy`` already wildcards ``/runs{path:path}``, so this
    new endpoint透传 to the frontend with zero proxy changes.
    """
    r = db.get(FactorRun, run_id)
    if r is None:
        raise ApiError("RUN_NOT_FOUND", f"运行 {run_id} 不存在")
    art = results_writer.read_run_trace(run_id)
    if art is None:
        # Pre-feature runs, or采集 disabled: return an empty-state, NOT a 404, so
        # the UI shows a friendly空态 instead of an error.
        return {
            "run_id": run_id,
            "available": False,
            "status": r.status,
            "reason": "此运行无过程追踪数据(可能在透视功能上线前执行,或采集已关闭)。重跑一次即可获得。",
        }
    art["available"] = True
    # Stamp the run's current status/degradation snapshot so the frontend can
    # derive every page-state from a single query.
    art["status"] = r.status
    art["degradation_level"] = getattr(r, "degradation_level", "none") or "none"
    art["degradation_count"] = getattr(r, "degradation_count", 0) or 0
    art["has_critical"] = bool(getattr(r, "has_critical", False))
    # 资源画像 meta (方案 §6): surface the run's factor/pool/window so the资源画像
    # 区块标题行 + workload归一化 can render without an extra query.
    art["factor_id"] = r.factor_id
    art["version"] = r.version
    return art


@router.get("/runs/{run_id}/resources/samples")
def run_resource_samples(
    run_id: str, db: Session = Depends(get_db), user=Depends(current_user)
) -> dict:
    """全分辨率 1Hz 样本 (资源画像方案 §3.4) — 前端首次缩放时懒加载一次.

    Decompresses the ``{run_id}.samples.json.gz`` artifact server-side and returns
    plain JSON; an absent artifact yields ``available:false`` (not a 404) so the
    timeline can fall back to its ≤300-point首屏 series gracefully.
    """
    r = db.get(FactorRun, run_id)
    if r is None:
        raise ApiError("RUN_NOT_FOUND", f"运行 {run_id} 不存在")
    art = resource_writer.read_samples(run_id)
    if art is None:
        return {"run_id": run_id, "available": False, "samples": []}
    art["available"] = True
    return art


@router.get("/runs/{run_id}/profile")
def run_profile(
    run_id: str, db: Session = Depends(get_db), user=Depends(current_user)
) -> dict:
    """函数级火焰图剖析数据 (资源画像方案 §3.5) — speedscope 兼容 + 预构火焰树.

    Returns 404 when the run was NOT profiled (no ``{run_id}.profile.json.gz``),
    so the frontend renders the「以剖析模式重跑」空态 (契约 §6).
    """
    r = db.get(FactorRun, run_id)
    if r is None:
        raise ApiError("RUN_NOT_FOUND", f"运行 {run_id} 不存在")
    art = resource_writer.read_profile(run_id)
    if art is None:
        raise ApiError("PROFILE_NOT_FOUND",
                       "此运行未开启性能剖析,无函数级火焰图数据", status_code=404)
    return art
