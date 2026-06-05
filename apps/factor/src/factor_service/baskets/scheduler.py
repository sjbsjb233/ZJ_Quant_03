"""Rolling tracking tick + orphan-run backstop — runs in the factor-worker
process (§6.3 兜底巡检 / §7.1 调度). Both are best-effort and never crash the
dispatcher loop.
"""
from __future__ import annotations

import datetime as dt
import logging
import uuid

from sqlalchemy.orm import Session

from quant_factor import EvalConfig
from quant_factor.cache import data_fingerprint

from ..db.models import Basket, BasketItemState, BasketRevision, BasketRun
from . import clock, keys
from .orchestrator import advance
from .runner import (
    active_revision,
    create_run,
    existing_brun_today,
    latest_ready_day,
)

_log = logging.getLogger("factor.basket_scheduler")


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


# ─────────────────────────────────────────────────────────────────────────────
def sweep_orphans(db: Session) -> int:
    """Advance any running basket_run whose children are all terminal (a crash
    can leave the parent 'running' with no finalize hook to push it)."""
    n = 0
    runs = db.query(BasketRun).filter(BasketRun.status == "running").all()
    for run in runs:
        try:
            advance(db, run.brun_id)
            n += 1
        except Exception:  # noqa: BLE001
            _log.debug("orphan advance failed %s", run.brun_id, exc_info=True)
    return n


# ─────────────────────────────────────────────────────────────────────────────
def _is_trading_day(reader, day: dt.date) -> bool:
    try:
        tbl = reader.read_calendar(start=day, end=day)
        if tbl.num_rows:
            vals = tbl["is_trading_day"].to_pylist()
            return bool(vals[0]) if vals else False
    except Exception:  # noqa: BLE001
        pass
    return day.weekday() < 5  # Mon-Fri fallback


def _basket_fingerprint(reader, shared: dict) -> str | None:
    try:
        today = clock.today()
        cfg = EvalConfig.from_dict(
            keys.eval_config_dict(shared, today - dt.timedelta(days=365), today)
        )
        return data_fingerprint(reader, cfg)
    except Exception:  # noqa: BLE001
        return None


def _fingerprint_changed(db: Session, basket: Basket, reader) -> bool:
    rev = active_revision(db, basket)
    if rev is None:
        return False
    fp_now = _basket_fingerprint(reader, dict(rev.shared_config or {}))
    if fp_now is None:
        return False
    st = (
        db.query(BasketItemState)
        .filter(BasketItemState.basket_id == basket.basket_id)
        .first()
    )
    if st is None or st.data_fingerprint is None:
        return False  # no prior compute → nothing to compare; full run handles it
    return st.data_fingerprint != fp_now


def record_system_revision(db: Session, basket: Basket, *, old_fp: str | None, new_fp: str | None) -> None:
    """Data-fingerprint change auto-files a system revision (§4.1 v1.3): same
    config/keys, author='system', invalidation=full."""
    rev = active_revision(db, basket)
    if rev is None:
        return
    new_no = basket.active_revision + 1
    db.add(BasketRevision(
        basket_id=basket.basket_id, revision=new_no,
        shared_config=dict(rev.shared_config or {}), items=list(rev.items or []),
        common_key=rev.common_key, comparable_key=rev.comparable_key,
        change_summary={"data_fingerprint": {"old": old_fp, "new": new_fp}},
        invalidation="full", reason="数据指纹变化触发重建", author="system",
    ))
    basket.active_revision = new_no
    db.commit()


# ─────────────────────────────────────────────────────────────────────────────
def tick_baskets(db: Session, reader, *, now: dt.datetime | None = None) -> int:
    """One scheduler pass — enqueue incremental ticks where due (§7.1)."""
    local = now or clock.now()
    today = local.date()
    enqueued = 0
    baskets = (
        db.query(Basket)
        .filter(Basket.tracking_enabled == True, Basket.status == "active")  # noqa: E712
        .all()
    )
    for b in baskets:
        if b.tracking_paused_reason:
            continue
        # SCH-08: a fixed-window basket cannot track (the window never moves).
        # Defensive skip even when tracking was force-enabled in the DB, bypassing
        # the API's TRACKING_ON_FIXED_WINDOW guard.
        rev = active_revision(db, b)
        if rev and keys.is_fixed((rev.shared_config or {}).get("window")):
            _log.warning("tick.skipped basket=%s reason=fixed_window_tracking", b.basket_id)
            continue
        if not _is_trading_day(reader, today):
            continue
        if (local.hour, local.minute) < (b.tracking_hour, b.tracking_minute):
            continue
        as_of = latest_ready_day(reader) or today
        # data-readiness gate BEFORE idempotency, so a behind-frontier day is
        # reported as data_not_ready rather than silently passing the idem check.
        if as_of < today:
            _log.info("tick.skipped basket=%s reason=data_not_ready as_of=%s",
                      b.basket_id, as_of)
            continue
        if existing_brun_today(db, b.basket_id, as_of):
            continue
        try:
            if _fingerprint_changed(db, b, reader):
                rev = active_revision(db, b)
                new_fp = _basket_fingerprint(reader, dict(rev.shared_config or {})) if rev else None
                old_st = db.query(BasketItemState).filter(
                    BasketItemState.basket_id == b.basket_id).first()
                record_system_revision(db, b, old_fp=old_st.data_fingerprint if old_st else None,
                                       new_fp=new_fp)
                create_run(db, b, mode="full", trigger="data_refingerprint", reader=reader,
                           initiated_by="system")
            else:
                create_run(db, b, mode="incremental", trigger="tracking", reader=reader,
                           initiated_by="system")
            b.last_tick_at = _now()
            db.commit()
            enqueued += 1
        except Exception:  # noqa: BLE001
            _log.warning("tick failed for basket %s", b.basket_id, exc_info=True)
            db.rollback()
    return enqueued
