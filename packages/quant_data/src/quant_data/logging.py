"""Lightweight structured logging for the data layer (全链路日志方案 v2.0 §4).

Zero external dependencies -- only the stdlib (``contextvars`` / ``time`` /
``sys`` / ``dataclasses``). The data layer must stay importable without
``quant_web``/SQLAlchemy/FastAPI (doc §2.2 "数据层零依赖业务"), so this module
only knows how to *emit* structured ``LogRecord`` objects. ``quant_web`` plugs
its own DB/SSE sink in at startup via :func:`register_sink`; until then logging
is a no-op beyond an optional stderr fallback.

Usage::

    from .logging import get_logger, timed
    _log = get_logger("quant_data.ingest")

    _log.info("ingest.batch.register", table=t, row_count=n)
    with timed(_log, "ingest.write", table=t):
        ...                       # auto-logs ingest.write.done {duration_ms}

The trace_id / actor that stitch the three layers together flow in through
:func:`bind_context` (called by the backend's TraceIdMiddleware) and are read
off ``contextvars`` automatically on every record.
"""
from __future__ import annotations

import contextlib
import contextvars
import dataclasses
import datetime as dt
import sys
import time
import traceback
from collections.abc import Callable, Iterator
from typing import Any

__all__ = [
    "LogRecord",
    "QDLogger",
    "bind_context",
    "reset_context",
    "get_context",
    "get_logger",
    "register_sink",
    "unregister_all_sinks",
    "set_threshold",
    "get_threshold",
    "set_stderr_fallback",
    "set_slow_query_ms",
    "get_slow_query_ms",
    "timed",
]

# --- levels -----------------------------------------------------------------
_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40, "CRITICAL": 50}

# Global emission threshold. DEBUG is dropped by default (doc §9.1: DEBUG is not
# stored in production). quant_web overrides this from Settings.log_level.
_threshold: int = _LEVELS["INFO"]

# When no sink is installed yet (or a sink raised), optionally echo to stderr so
# a standalone ``import quant_data`` session is not silent. Off by default to
# keep the test/library experience quiet; quant_web leaves it off too (it owns
# the real sinks).
_stderr_fallback: bool = False

# Reads slower than this emit reader.query.slow (doc §4.2.2, §5.7 LOG_SLOW_QUERY_MS).
_slow_query_ms: int = 2000


def set_slow_query_ms(ms: int) -> None:
    _set("_slow_query_ms", int(ms))


def get_slow_query_ms() -> int:
    return _slow_query_ms


def set_threshold(level: str) -> None:
    """Set the minimum level that is emitted (doc §5.7 LOG_LEVEL)."""
    _set("_threshold", _LEVELS[level.upper()])


def get_threshold() -> str:
    for name, num in _LEVELS.items():
        if num == _threshold:
            return name
    return "INFO"


def set_stderr_fallback(enabled: bool) -> None:
    _set("_stderr_fallback", bool(enabled))


def _set(name: str, value: Any) -> None:
    globals()[name] = value


# --- context (trace_id / actor / request_id ...) ----------------------------
_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar("quant_data_log_ctx", default={})

Token = contextvars.Token


def bind_context(**kv: Any) -> Token:
    """Merge ``kv`` into the current logging context; return a reset token.

    The token must be passed back to :func:`reset_context` (typically in a
    ``finally``) so per-request values never leak into a pooled worker thread.
    """
    merged = {**_ctx.get(), **{k: v for k, v in kv.items() if v is not None}}
    return _ctx.set(merged)


def reset_context(token: Token) -> None:
    try:
        _ctx.reset(token)
    except (ValueError, LookupError):  # pragma: no cover - token from a foreign ctx
        _ctx.set({})


def get_context() -> dict:
    """A copy of every key currently bound (trace_id, actor, request_id, ...)."""
    return dict(_ctx.get())


# --- sinks ------------------------------------------------------------------
_sinks: list[Callable[["LogRecord"], None]] = []


def register_sink(fn: Callable[["LogRecord"], None]) -> None:
    """Register a record consumer (quant_web installs a DB+SSE sink here)."""
    _sinks.append(fn)


def unregister_all_sinks() -> None:
    _sinks.clear()


# --- record -----------------------------------------------------------------
@dataclasses.dataclass
class LogRecord:
    """One structured log line (doc §4.1.2)."""

    ts: str
    level: str
    logger: str
    event: str
    trace_id: str | None = None
    actor: str | None = None
    request_id: str | None = None
    duration_ms: int | None = None
    extra: dict | None = None
    exc_type: str | None = None
    exc_message: str | None = None
    stack: str | None = None

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _dispatch(record: "LogRecord") -> None:
    """Fan a record out to every sink; never let a sink break the caller."""
    delivered = False
    for sink in list(_sinks):
        try:
            sink(record)
            delivered = True
        except Exception:  # noqa: BLE001 - logging must never break business code
            _emit_stderr(record, sink_failed=True)
    if not delivered and _stderr_fallback:
        _emit_stderr(record)


def _emit_stderr(record: "LogRecord", *, sink_failed: bool = False) -> None:
    try:
        prefix = "[qd-log-sink-failed] " if sink_failed else "[qd-log] "
        bits = [record.ts, record.level, record.logger, record.event]
        if record.trace_id:
            bits.append(f"trace={record.trace_id}")
        if record.exc_type:
            bits.append(f"{record.exc_type}: {record.exc_message}")
        sys.stderr.write(prefix + " ".join(str(b) for b in bits) + "\n")
    except Exception:  # noqa: BLE001 - last-resort path, swallow everything
        pass


# --- logger -----------------------------------------------------------------
class QDLogger:
    """A named logger. Cheap to construct; reuse via :func:`get_logger`."""

    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name

    def _log(
        self,
        level: str,
        event: str,
        *,
        exc: BaseException | None = None,
        capture_exc: bool = False,
        duration_ms: int | None = None,
        extra: dict | None = None,
    ) -> None:
        if _LEVELS[level] < _threshold:
            return
        ctx = _ctx.get()
        exc_type = exc_message = stack = None
        if exc is not None:
            exc_type = type(exc).__name__
            exc_message = str(exc)
            stack = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        elif capture_exc:
            etype, evalue, etb = sys.exc_info()
            if etype is not None:
                exc_type = etype.__name__
                exc_message = str(evalue)
                stack = "".join(traceback.format_exception(etype, evalue, etb))
        record = LogRecord(
            ts=_now_iso(),
            level=level,
            logger=self.name,
            event=event,
            trace_id=ctx.get("trace_id"),
            actor=ctx.get("actor"),
            request_id=ctx.get("request_id"),
            duration_ms=duration_ms,
            extra=extra or None,
            exc_type=exc_type,
            exc_message=exc_message,
            # only ERROR/CRITICAL carry a stack (doc §4.1.2)
            stack=stack if _LEVELS[level] >= _LEVELS["ERROR"] else None,
        )
        _dispatch(record)

    def debug(self, event: str, **extra: Any) -> None:
        self._log("DEBUG", event, duration_ms=extra.pop("duration_ms", None), extra=extra)

    def info(self, event: str, **extra: Any) -> None:
        self._log("INFO", event, duration_ms=extra.pop("duration_ms", None), extra=extra)

    def warn(self, event: str, **extra: Any) -> None:
        self._log("WARN", event, duration_ms=extra.pop("duration_ms", None), extra=extra)

    # alias for callers used to the stdlib name
    warning = warn

    def error(self, event: str, exc: BaseException | None = None, **extra: Any) -> None:
        self._log("ERROR", event, exc=exc, duration_ms=extra.pop("duration_ms", None), extra=extra)

    def critical(self, event: str, exc: BaseException | None = None, **extra: Any) -> None:
        self._log("CRITICAL", event, exc=exc, duration_ms=extra.pop("duration_ms", None), extra=extra)

    def exception(self, event: str, **extra: Any) -> None:
        """Log an ERROR with the active ``sys.exc_info()`` stack attached."""
        self._log("ERROR", event, capture_exc=True, duration_ms=extra.pop("duration_ms", None),
                  extra=extra)


_loggers: dict[str, QDLogger] = {}


def get_logger(name: str) -> QDLogger:
    logger = _loggers.get(name)
    if logger is None:
        logger = QDLogger(name)
        _loggers[name] = logger
    return logger


# --- timing helper ----------------------------------------------------------
@contextlib.contextmanager
def timed(logger: QDLogger, event: str, level: str = "INFO", **extra: Any) -> Iterator[dict]:
    """Time a block and emit ``{event}.done`` with ``duration_ms`` on success.

    Yields a mutable dict so the body can attach measured values (row counts,
    byte sizes) that are merged into the final record::

        with timed(_log, "ingest.write", table=t) as m:
            m["row_count"] = write(...)
    """
    start = time.perf_counter()
    bag: dict = dict(extra)
    ok = False
    try:
        yield bag
        ok = True
    finally:
        # only emit `{event}.done` on the success path -- a raising block must not
        # leave a misleading ".done" in the trace; the caller logs the failure.
        if ok:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger._log(level, f"{event}.done", duration_ms=duration_ms, extra=bag or None)
