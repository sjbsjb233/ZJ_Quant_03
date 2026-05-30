"""Observability wiring for data-keeper (容器化重构 V3 §6.7).

quant_data inside its own container can no longer reach quant_web's in-process
``AppLogStore`` sink. We install an :class:`HttpLogShipper` instead: every
data-layer ``LogRecord`` (ingest / write / QC / migration, plus keeper's own
``keeper.*`` loggers) is written to a local JSONL file *and* shipped to
web-backend's ``/logs/ingest`` as ``source="data"`` — joined by trace_id into the
same ledger, SSE stream and ``/logs/trace/{id}`` view as backend/frontend logs.

If ``LOG_INGEST_URL`` is unset (e.g. a standalone run / test), shipping is a
no-op and only the stderr/JSONL fallback applies — keeper never depends on web.
"""
from __future__ import annotations

from quant_data import logging as qd_logging
from quant_data.log_shipper import HttpLogShipper

from .config import Settings

_shipper: HttpLogShipper | None = None


def install_obs(settings: Settings) -> HttpLogShipper | None:
    global _shipper
    qd_logging.set_threshold(settings.log_level)
    if not settings.log_ingest_url:
        # No central collector configured — keep quant_data emitting to its own
        # stderr fallback so a standalone keeper still surfaces logs.
        qd_logging.set_stderr_fallback(True)
        return None
    _shipper = HttpLogShipper(
        url=settings.log_ingest_url,
        token=settings.log_ingest_token,
        source="data",
        jsonl_dir=settings.log_file_dir,
    )
    qd_logging.register_sink(_shipper.sink)
    return _shipper


def get_shipper() -> HttpLogShipper | None:
    return _shipper


def stop_obs() -> None:
    if _shipper is not None:
        _shipper.stop()
