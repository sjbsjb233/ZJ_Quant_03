"""Wire the data layer's logger into the backend store (全链路日志方案 v2.0 §4.4).

``quant_data`` stays dependency-free: it only knows how to emit ``LogRecord``s
through registered sinks. At startup the backend registers one sink that maps a
data-layer record into an app_log row (``source="data"``, the V3 §6.6 unified
data-layer source — also used by the out-of-process data-keeper shipper) and
enqueues it -- so a parquet write / duckdb query / migration logs land in the
same ledger and SSE stream as backend + frontend logs, joined by trace_id.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from quant_data import logging as qd_logging

from . import store

if TYPE_CHECKING:  # pragma: no cover
    from ..config import Settings


def install_sinks(settings: "Settings") -> None:
    # propagate the configured thresholds into the data layer
    qd_logging.set_threshold(settings.log_level)
    qd_logging.set_slow_query_ms(settings.log_slow_query_ms)

    def sink(record: "qd_logging.LogRecord") -> None:
        st = store.get_store()
        if st is None:
            return
        try:
            row = record.as_dict()
            row["source"] = "data"
            st.enqueue(row)
        except Exception:  # noqa: BLE001 - a failing sink must never break the data layer
            pass

    qd_logging.register_sink(sink)
