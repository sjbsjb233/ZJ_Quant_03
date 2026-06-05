"""Create + expand a basket run — shared by the API (manual) and the tracking
scheduler (auto). One place resolves the window and fans out child tasks."""
from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from ..db.models import Basket, BasketRevision, BasketRun, BasketRunItem, FactorRun
from . import clock, keys
from .orchestrator import expand

_log = logging.getLogger("factor.basket_runner")


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


def latest_ready_day(reader) -> dt.date | None:
    """The data frontier — latest ready trading day in bars_daily (§7.1).

    NB: the real DataReader requires the *logical* table name ``raw.bars_daily``
    (``get_spec`` raises KeyError on the bare ``bars_daily``); passing the short
    name silently swallowed the error and fell back to the real clock — a bug the
    FakeReader (which ignores the name) hid until the live warehouse exercised it.
    """
    if reader is None:
        return None
    for name in ("raw.bars_daily", "bars_daily"):
        try:
            d = reader.latest_date_in_view(name)
            if d is not None:
                return d
        except Exception:  # noqa: BLE001 — try the next spelling, else None
            continue
    return None


def _trading_days(reader, start: dt.date, end: dt.date) -> list[dt.date] | None:
    try:
        tbl = reader.read_calendar(start=start, end=end)
        import pyarrow.compute as pc  # local — reader returns arrow
        mask = pc.equal(tbl["is_trading_day"], True)
        days = tbl.filter(mask)["date"].to_pylist()
        return [d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d)[:10]) for d in days]
    except Exception:  # noqa: BLE001
        return None


def active_revision(db: Session, basket: Basket) -> BasketRevision | None:
    return (
        db.query(BasketRevision)
        .filter(BasketRevision.basket_id == basket.basket_id,
                BasketRevision.revision == basket.active_revision)
        .first()
    )


def _ensure_item_keys(items: list[dict], common: str) -> list[dict]:
    out = []
    for it in items:
        it = dict(it)
        if not it.get("item_key"):
            it["item_key"] = keys.item_key(common, it["factor_id"], it.get("version") or "",
                                           it.get("params") or {})
        out.append(it)
    return out


def create_run(
    db: Session,
    basket: Basket,
    *,
    mode: str = "full",
    trigger: str = "manual",
    with_correlation: bool | None = None,
    initiated_by: str = "",
    trace_id: str | None = None,
    reader=None,
    repair_item_ids: list[str] | None = None,
) -> BasketRun:
    rev = active_revision(db, basket)
    if rev is None:
        raise ValueError("basket has no active revision")
    shared = dict(rev.shared_config or {})
    window = shared.get("window")
    common = rev.common_key or keys.common_key(shared)

    as_of = latest_ready_day(reader) or clock.today()
    if keys.is_fixed(window):
        rstart, rend = keys.resolve_window(window, as_of)
        as_of = rend
    else:
        tdays = None
        try:
            _, unit = keys.parse_span(keys.normalize_window(window)["span"])
            if unit == "td":
                tdays = _trading_days(reader, as_of - dt.timedelta(days=800), as_of)
        except Exception:  # noqa: BLE001
            tdays = None
        rstart, rend = keys.resolve_window(window, as_of, trading_days=tdays)

    with_corr = basket.correlation_enabled if with_correlation is None else with_correlation
    if mode == "corr_only":
        with_corr = True

    brun = BasketRun(
        brun_id=f"brun_{uuid.uuid4().hex[:16]}",
        basket_id=basket.basket_id,
        revision=rev.revision,
        mode=mode,
        trigger=trigger,
        with_correlation=with_corr,
        resolved_start=rstart,
        resolved_end=rend,
        as_of=as_of,
        status="queued",
        initiated_by=initiated_by,
        trace_id=trace_id,
        started_at=_now(),
    )
    db.add(brun)
    db.flush()  # assign without committing — expand commits brun + children atomically

    items = _ensure_item_keys(list(rev.items or []), common)
    if mode == "corr_only":
        eval_items: list[dict] = []
    elif mode == "repair":
        rset = set(repair_item_ids or [])
        eval_items = [it for it in items if it["item_id"] in rset]
    else:
        eval_items = items

    expand(db, brun, items=eval_items, shared_config=shared,
           initiated_by=initiated_by, trace_id=trace_id)
    _log.info("brun.submit %s basket=%s mode=%s trigger=%s n=%d",
              brun.brun_id, basket.basket_id, mode, trigger, len(eval_items))
    return brun


def existing_brun_today(db: Session, basket_id: str, as_of: dt.date) -> BasketRun | None:
    """Idempotency guard (§7.1): same basket + same as_of with a non-failed run."""
    return (
        db.query(BasketRun)
        .filter(BasketRun.basket_id == basket_id, BasketRun.as_of == as_of,
                BasketRun.status != "failed", BasketRun.status != "canceled")
        .first()
    )


def failed_item_ids(db: Session, brun_id: str) -> list[str]:
    rows = (
        db.query(BasketRunItem)
        .filter(BasketRunItem.brun_id == brun_id, BasketRunItem.status == "failed")
        .all()
    )
    return [r.item_id for r in rows]
