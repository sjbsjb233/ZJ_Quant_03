"""Result-reading endpoints — IC series / layered / decay / turnover."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db.models import EvalSummary, FactorRun
from ..deps import current_user, get_db
from ..errors import ApiError
from ..services import results_writer


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
