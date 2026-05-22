"""Backend structured logger (全链路日志方案 v2.0 §5.5).

``app_logger`` is the top-level instance routes / middleware / errors use. Every
call is stamped with the active request's trace_id / actor / route (read off the
context vars in :mod:`.context`), tagged ``source="backend"``, and handed to the
``AppLogStore`` (async DB + file) and the SSE stream. If no store is wired yet
(or it failed), records fall back to stderr so nothing is silently lost.
"""
from __future__ import annotations

import datetime as dt
import sys
import traceback
from typing import Any

from . import context, store

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40, "CRITICAL": 50}
_threshold = _LEVELS["INFO"]

# extra keys that map onto dedicated app_log columns (not the JSON `extra` blob)
_COLUMN_KEYS = {"route", "method", "status_code", "duration_ms", "session_id",
                "message", "host", "request_id", "actor", "trace_id"}


def configure(level: str) -> None:
    global _threshold
    _threshold = _LEVELS.get(level.upper(), _LEVELS["INFO"])


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


class BackendLogger:
    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name

    def _emit(self, level: str, event: str, *, exc: BaseException | None = None,
              capture_exc: bool = False, extra: dict | None = None) -> None:
        if _LEVELS[level] < _threshold:
            return
        extra = dict(extra or {})
        cols = {k: extra.pop(k) for k in list(extra) if k in _COLUMN_KEYS}

        exc_type = exc_message = stack = None
        if exc is not None:
            exc_type, exc_message = type(exc).__name__, str(exc)
            stack = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        elif capture_exc:
            et, ev, tb = sys.exc_info()
            if et is not None:
                exc_type, exc_message = et.__name__, str(ev)
                stack = "".join(traceback.format_exception(et, ev, tb))

        record = {
            "ts": _now(),
            "level": level,
            "logger": self.name,
            "event": event,
            "source": "backend",
            "trace_id": cols.get("trace_id") if "trace_id" in cols else context.trace_id_var.get(),
            "request_id": cols.get("request_id") or context.request_id_var.get(),
            "actor": cols.get("actor") or context.actor_var.get(),
            "route": cols.get("route") or context.route_var.get(),
            "method": cols.get("method") or context.method_var.get(),
            "status_code": cols.get("status_code"),
            "duration_ms": cols.get("duration_ms"),
            "session_id": cols.get("session_id"),
            "message": cols.get("message"),
            "extra": extra or None,
            "exc_type": exc_type,
            "exc_message": exc_message,
            "stack": stack if _LEVELS[level] >= _LEVELS["ERROR"] else None,
        }
        st = store.get_store()
        if st is None:
            _stderr(record)
            return
        try:
            st.enqueue(record)
        except Exception:  # noqa: BLE001 - logging must never break a request
            _stderr(record)

    def debug(self, event: str, **extra: Any) -> None:
        self._emit("DEBUG", event, extra=extra)

    def info(self, event: str, **extra: Any) -> None:
        self._emit("INFO", event, extra=extra)

    def warn(self, event: str, **extra: Any) -> None:
        self._emit("WARN", event, extra=extra)

    warning = warn

    def error(self, event: str, exc: BaseException | None = None, **extra: Any) -> None:
        self._emit("ERROR", event, exc=exc, extra=extra)

    def critical(self, event: str, exc: BaseException | None = None, **extra: Any) -> None:
        self._emit("CRITICAL", event, exc=exc, extra=extra)

    def exception(self, event: str, **extra: Any) -> None:
        self._emit("ERROR", event, capture_exc=True, extra=extra)


def _stderr(record: dict) -> None:
    try:
        bits = [str(record.get(k)) for k in ("ts", "level", "logger", "event") if record.get(k)]
        if record.get("trace_id"):
            bits.append(f"trace={record['trace_id']}")
        sys.stderr.write("[app-log] " + " ".join(bits) + "\n")
    except Exception:  # noqa: BLE001
        pass


_loggers: dict[str, BackendLogger] = {}


def get_logger(name: str) -> BackendLogger:
    logger = _loggers.get(name)
    if logger is None:
        logger = BackendLogger(name)
        _loggers[name] = logger
    return logger


app_logger = get_logger("quant_web")
