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
    BasketRevision,
    Task,
)
from ..queue import store
from . import keys
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
            brun_id=run.brun_id, seq_no=seq, gate="ready", commit=False,
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
        # repair 只重算 previously-FAILED 子集,但相关性必须覆盖整篮(其余 item 的台账
        # 已在盘上),否则相关矩阵/高相关对/聚类只剩重算子集 → 低相关贪心剪枝工作台得到
        # 错误冗余度。无台账的 item 由相关执行器隐式排除。
        if run.mode == "repair":
            corr_payload_items = _full_revision_corr_items(db, run) or corr_payload_items
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
            brun_id=run.brun_id, seq_no=9999, gate=gate, commit=False,
        )
        run.corr_task_id = ctask.task_id

    run.total_children = n_eval + (1 if run.with_correlation else 0)
    run.status = "queued"
    # ONE atomic commit — brun + all child tasks + items become visible together,
    # so the worker never sees a child before the brun is fully finalized.
    db.commit()


def _full_revision_corr_items(db: Session, run: BasketRun) -> list[dict[str, Any]]:
    """整篮相关性的 payload(item_id/item_key/label),覆盖该 revision 的全部 item。

    item_key 与 runner._ensure_item_keys 同口径计算(common_key + keys.item_key),
    以便相关执行器读到与 eval 落盘一致的台账目录。无台账的 item 由执行器隐式排除。
    """
    rev = (
        db.query(BasketRevision)
        .filter(BasketRevision.basket_id == run.basket_id,
                BasketRevision.revision == run.revision)
        .first()
    )
    if rev is None:
        return []
    common = rev.common_key or keys.common_key(rev.shared_config)
    out: list[dict[str, Any]] = []
    for it in rev.items or []:
        ik = it.get("item_key") or keys.item_key(
            common, it["factor_id"], it.get("version") or "", it.get("params") or {})
        out.append({"item_id": it.get("item_id"), "item_key": ik, "label": it.get("label")})
    return out


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
        # 修复运行的整篮相关性 payload 已被 expand() 扩到全 revision(覆盖全部 item,
        # 无台账的由执行器隐式排除),故闸门须按全篮可用 item 数判定,而非仅本次重算
        # 子集——否则一个只重算 <2 个 item 的修复会误判 ok<2 而取消整篮相关性,正是
        # corr-scope 扩展想避免的退化。
        if run.mode == "repair" and run.with_correlation:
            avail = len(_full_revision_corr_items(db, run))
        else:
            avail = len(ok)
        if eval_states and avail < 2:
            _cancel_task(db, corr, "insufficient_ok_items")
            _log.info("brun.corr.cancel %s (avail<2)", brun_id)
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
    if basket.stale_scope == "none":
        return
    if run.mode == "full":
        # 全量重建覆盖整个 item 集合 → 完全清除失效标记。
        basket.stale_scope = "none"
        basket.stale_items = []
        basket.stale_reason = None
        if basket.tracking_paused_reason in ("config_stale", "data_refingerprint"):
            basket.tracking_paused_reason = None
    elif run.mode == "repair":
        # repair 只重算 previously-FAILED 子集,与 revision 保存写入的 config-stale
        # item 集合相互独立。只把本次实际重算的 item 从 stale_items 中移除;若仍有
        # 未重建的 stale item,保持 partial 并继续暂停追踪(否则会在仍 stale 的
        # 配置下滚动累计台账 → 把陈旧数据当新鲜)。
        repaired = {b.item_id for b in
                    db.query(BasketRunItem)
                    .filter(BasketRunItem.brun_id == run.brun_id)
                    .all()}
        remaining = [i for i in (basket.stale_items or []) if i not in repaired]
        if remaining:
            basket.stale_items = remaining
            # stale_scope 维持 'partial';不解除暂停。
        else:
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
