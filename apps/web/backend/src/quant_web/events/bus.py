"""A minimal synchronous pub/sub bus (doc §8.4).

This period only the data-management module *publishes* events; the factor /
strategy / monitor modules will subscribe later. Implemented in-process so there
is no Redis dependency yet -- the publish/subscribe surface is what matters for
forward-compatibility.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from ..logging import app_logger

log = app_logger

# the full event vocabulary (doc §8.4); only data.* fire this period
EVENT_TYPES = {
    "batch.registered", "batch.finalized", "batch.invalidated", "batch.superseded",
    "source.registered", "source.updated", "source.deactivated",
    "field.registered", "field.promoted", "field.deprecated",
    "quality.issue_detected", "quality.acknowledged",
    "maintenance.vacuum_executed", "maintenance.compact_executed",
    "factor.recomputed", "factor.ic_alert",
    "strategy.backtest_completed", "strategy.pnl_alert",
    "monitor.alert_triggered", "monitor.alert_resolved",
}

_subscribers: dict[str, list[Callable]] = defaultdict(list)


def subscribe(event: str, fn: Callable) -> None:
    _subscribers[event].append(fn)


def publish(event: str, payload: dict | None = None) -> None:
    subscribers = _subscribers.get(event, [])
    log.debug("event.published", topic=event, subscribers=len(subscribers))
    for fn in subscribers:
        try:
            fn(payload or {})
        except Exception:  # noqa: BLE001 - a bad subscriber must not break the publisher
            log.exception("event.subscriber.failed", topic=event)
