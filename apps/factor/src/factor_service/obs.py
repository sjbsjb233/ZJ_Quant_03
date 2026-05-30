"""Observability wiring for factor-api / factor-worker (容器化重构 V3 §6.7).

Until now factor only logged to stderr; this is the first time its logs join the
unified ``app_log``. We install an :class:`HttpLogShipper` (source="factor") and:

  1. register it as a ``quant_data.logging`` sink — so data-layer reads the factor
     service performs (DataReader) ship with the request's trace_id;
  2. attach a stdlib ``logging.Handler`` that bridges factor's own ``logging``
     calls into the same shipper (filtered to factor namespaces + any WARN+), so
     ``factor.dispatcher`` / ``factor_service.*`` lines also land in the ledger,
     joined by the same trace_id (set by the middleware / dispatcher).

If ``LOG_INGEST_URL`` is unset (standalone dev / tests) shipping is a no-op and
only the stderr fallback applies — factor never hard-depends on web.
"""
from __future__ import annotations

import datetime as dt
import logging

from quant_data import logging as qd_logging
from quant_data.log_shipper import HttpLogShipper

from .config import Settings
from .logging_ctx import trace_id_var

_shipper: HttpLogShipper | None = None

_STDLIB_LEVEL = {
    "DEBUG": "DEBUG", "INFO": "INFO", "WARNING": "WARN", "WARN": "WARN",
    "ERROR": "ERROR", "CRITICAL": "CRITICAL",
}


class _ShipperHandler(logging.Handler):
    """Bridge stdlib LogRecords into the HttpLogShipper as source='factor'."""

    def __init__(self, shipper: HttpLogShipper):
        super().__init__()
        self._shipper = shipper

    def emit(self, record: logging.LogRecord) -> None:
        try:
            name = record.name or "factor"
            # only ship factor's own logs, plus WARN+ from any library
            if not (name.startswith("factor") or record.levelno >= logging.WARNING):
                return
            row = {
                "ts": dt.datetime.utcfromtimestamp(record.created)
                .strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
                "level": _STDLIB_LEVEL.get(record.levelname, record.levelname),
                "logger": name[:64],
                "event": (name.split(".")[-1] or "log")[:128],
                "message": record.getMessage(),
                "trace_id": trace_id_var.get(),
                "source": "factor",
            }
            if record.exc_info and record.exc_info[0] is not None:
                row["exc_type"] = record.exc_info[0].__name__
                row["exc_message"] = str(record.exc_info[1])
            self._shipper.ship_row(row)
        except Exception:  # noqa: BLE001 - logging must never break business code
            pass


def install_obs(settings: Settings) -> HttpLogShipper | None:
    global _shipper
    qd_logging.set_threshold(settings.log_level)
    if not settings.log_ingest_url:
        qd_logging.set_stderr_fallback(True)
        return None
    _shipper = HttpLogShipper(
        url=settings.log_ingest_url,
        token=settings.log_ingest_token,
        source="factor",
        jsonl_dir=settings.log_file_dir,
    )
    qd_logging.register_sink(_shipper.sink)                 # data-layer logs (with trace_id)
    handler = _ShipperHandler(_shipper)
    handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(handler)                 # factor's own stdlib logs
    return _shipper


def get_shipper() -> HttpLogShipper | None:
    return _shipper


def stop_obs() -> None:
    if _shipper is not None:
        _shipper.stop()
