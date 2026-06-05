"""多级任务编排 — expand a basket_run into child tasks, and advance() it to
terminal (因子层重构 §6).

The "big task" (basket_run) is a business row, NOT a tasks row. Children are
real tasks. ``advance`` is idempotent and re-entrant: it derives everything from
DB rows, so a crash + reaper recovery naturally resumes it.
"""
from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy.orm import Session

from ..db.models import (
    BasketRun,
    BasketRunItem,
    BasketItemState,
    Basket,
    Task,
)
from ..queue import store
from .snapshots import build_snapshot

_log = logging.getLogger("factor.orchestrator")

TASK_TERMINAL = {"success", "failed", "canceled"}
CHILD_TERMINAL = {"success", "degraded", "partial", "failed", "canceled"}
RUN_TERMINAL = {"success", "degraded", "partial", "failed", "canceled"}


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


def lane_for(trigger: str) -> str:
    """tracking ticks ride the routine lane; everything else is interactive."""
    return "routine" if trigger in ("tracking", "data_refingerprint") else "interactive"


# ─────────────────────────────────────────────────────────────────────────────
def expand(
    db: Session,
    run: BasketRun,
    *,
    items: list[dict[str, Any]],
    shared_config: dict[str, Any],
    initiated_by: str = "",
    trace_id: str | None = None,
) -> None:
    """Create the child tasks for a basket run (§6.2). Each entry → one
    basket_eval; if with_correlation → a trailing basket_corr gated 'waiting'."""
    lane = lane_for(run.trigger)
    rs = run.resolved_start.isoformat() if run.resolved_start else None
    re_ = run.resolved_end.isoformat() if run.resolved_end else None

    eval_items = sorted(items, key=lambda x: x.get("sort") if x.get("sort") is not None else 0)
    seq = 0
    corr_payload_items = []
    for it in eval_items:
        seq += 1
        payload = {
            "brun_id": run.brun_id,
            "basket_id": run.basket_id,
            "revision": run.revision,
            "mode": run.mode,
            "item_id": it["item_id"],
            "item_key": it["item_key"],
            "factor_id": it["factor_id"],
            "version": it.get("version"),
            "params": it.get("params") or {},
            "label": it.get("label"),
            "shared_config": shared_config,
            "resolved_start": rs,
            "resolved_end": re_,
        }
        task = store.enqueue(
            db, task_type="basket_eval", payload=payload, lane=lane,
            factor_id=it["factor_id"], initiated_by=initiated_by, trace_id=trace_id,
            brun_id=run.brun_id, seq_no=seq, gate="ready",
        )
        db.add(BasketRunItem(
            brun_id=run.brun_id, item_id=it["item_id"], item_key=it["item_key"],
            factor_id=it["factor_id"], seq_no=seq, task_id=task.task_id, status="queued",
        ))
        corr_payload_items.append({"item_id": it["item_id"], "item_key": it["item_key"],
                                   "label": it.get("label")})

    n_eval = seq
    if run.with_correlation:
        basket = db.get(Basket, run.basket_id)
        corr_payload = {
            "brun_id": run.brun_id,
            "basket_id": run.basket_id,
            "revision": run.revision,
            "items": corr_payload_items,
            "method": basket.correlation_method if basket else "pearson",
            "threshold": basket.correlation_threshold if basket else 0.7,
            "min_periods": basket.corr_min_periods if basket else 12,
            "resolved_start": rs,
            "resolved_end": re_,
        }
        # corr_only has no evals to wait for → ready immediately.
        gate = "ready" if run.mode == "corr_only" else "waiting"
        ctask = store.enqueue(
            db, task_type="basket_corr", payload=corr_payload, lane=lane,
            initiated_by=initiated_by, trace_id=trace_id,
            brun_id=run.brun_id, seq_no=9999, gate=gate,
        )
        run.corr_task_id = ctask.task_id

    run.total_children = n_eval + (1 if run.with_correlation else 0)
    run.status = "queued"
    db.commit()


# ─────────────────────────────────────────────────────────────────────────────
def _eff_status(db: Session, bri: BasketRunItem) -> str:
    # The TASK's live state is authoritative (review C2): a child whose task is
    # still queued/running — including one re-queued for auto-retry after a
    # failed attempt — is NOT terminal, even though the executor already stamped
    # bri.status='failed' before raising. Trusting a stale terminal bri.status
    # here would finalize the parent prematurely and discard the retry's result.
    t = db.get(Task, bri.task_id) if bri.task_id else None
    if t is not None:
        if t.status in ("queued", "running"):
            return "running"          # in-flight or retrying → not terminal
        if t.status == "failed":
            return "failed"
        if t.status == "canceled":
            return "canceled"
        if t.status == "success":     # task done → trust the richer run outcome
            return bri.status if bri.status in ("success", "degraded") else "success"
    return bri.status if bri.status in CHILD_TERMINAL else (bri.status or "queued")


def map_parent_status(eval_states: list[str]) -> str:
    if not eval_states:
        return "success"  # corr_only / empty
    if all(s == "success" for s in eval_states):
        return "success"
    has_ok = any(s in ("success", "degraded") for s in eval_states)
    # A canceled child produced no result — for parent completeness it counts as
    # incomplete (like failed), NOT as a degradation (设计 §6.3 + ORC-01).
    has_incomplete = any(s in ("failed", "canceled") for s in eval_states)
    if not has_incomplete:
        return "degraded"      # only success + degraded
    if has_ok:
        return "partial"       # some usable, some incomplete
    return "failed"            # nothing usable


def advance(db: Session, brun_id: str) -> None:
    """Recompute progress, open/cancel the corr gate, finalize at terminal (§6.3)."""
    # The dispatcher reuses ONE long-lived Session with expire_on_commit=False,
    # while basket children commit their run_id/status from a *separate* worker
    # session. Without this, the identity map serves stale BasketRunItem rows
    # (run_id=None) and build_snapshot reads empty metrics (review C1). Expire
    # first so every read below hits the DB fresh.
    db.expire_all()
    run = db.get(BasketRun, brun_id)
    if run is None or run.status in RUN_TERMINAL:
        return
    bris = (
        db.query(BasketRunItem).filter(BasketRunItem.brun_id == brun_id).all()
    )
    eval_states = [_eff_status(db, b) for b in bris]
    # sync effective states back onto the rows (so列表页 immune to join)
    for b, s in zip(bris, eval_states):
        if b.status != s:
            b.status = s

    corr = None
    if run.corr_task_id:
        corr = db.get(Task, run.corr_task_id)

    run.done_children = sum(1 for s in eval_states if s in CHILD_TERMINAL) + (
        1 if (corr is not None and corr.status in TASK_TERMINAL) else 0
    )
    run.failed_children = sum(1 for s in eval_states if s == "failed")
    run.degraded_children = sum(1 for s in eval_states if s == "degraded")
    if run.status == "queued":
        run.status = "running"

    evals_terminal = all(s in CHILD_TERMINAL for s in eval_states)
    if evals_terminal and corr is not None and corr.gate == "waiting":
        ok = [s for s in eval_states if s in ("success", "degraded")]
        if eval_states and len(ok) < 2:
            _cancel_task(db, corr, "insufficient_ok_items")
            _log.info("brun.corr.cancel %s (ok<2)", brun_id)
        else:
            corr.gate = "ready"
            _log.info("brun.corr.gate_open %s", brun_id)
    db.commit()

    # terminal check (re-read corr state after a possible cancel)
    corr_terminal = (corr is None) or (corr.status in TASK_TERMINAL)
    if evals_terminal and corr_terminal:
        run.status = map_parent_status(eval_states)
        if run.status in ("success", "degraded", "partial") and not run.snapshot_id:
            try:
                run.snapshot_id = build_snapshot(db, run, corr_json=None)
            except Exception as e:  # noqa: BLE001
                run.error = (run.error or "") + f" · snapshot_build_failed: {e}"
                _log.warning("snapshot build failed for %s: %s", brun_id, e, exc_info=True)
        run.finished_at = run.finished_at or _now()
        if run.started_at and run.finished_at:
            run.duration_sec = (run.finished_at - run.started_at).total_seconds()
        _resume_tracking_if_done(db, run)
        db.commit()
        _log.info("brun.done %s status=%s snapshot=%s", brun_id, run.status, run.snapshot_id)


def _cancel_task(db: Session, task: Task, reason: str) -> None:
    task.status = "canceled"
    task.error = reason
    task.finished_at = _now()


def _resume_tracking_if_done(db: Session, run: BasketRun) -> None:
    """A successful full/partial rebuild clears stale + resumes tracking (§9.1)."""
    if run.mode not in ("full", "incremental", "repair"):
        return
    if run.status not in ("success", "degraded", "partial"):
        return
    basket = db.get(Basket, run.basket_id)
    if basket is None:
        return
    if run.mode in ("full", "repair") and basket.stale_scope != "none":
        basket.stale_scope = "none"
        basket.stale_items = []
        basket.stale_reason = None
        if basket.tracking_paused_reason in ("config_stale", "data_refingerprint"):
            basket.tracking_paused_reason = None


def cancel_run(db: Session, run: BasketRun, *, actor: str = "") -> None:
    """Cancel an entire generation (§6.5) — no snapshot produced."""
    tasks = db.query(Task).filter(Task.brun_id == run.brun_id).all()
    for t in tasks:
        if t.status == "queued":
            t.status = "canceled"
            t.finished_at = _now()
        elif t.status == "running":
            t.cancel_requested = True
    run.status = "canceled"
    run.finished_at = _now()
    if run.started_at:
        run.duration_sec = (run.finished_at - run.started_at).total_seconds()
    db.commit()
