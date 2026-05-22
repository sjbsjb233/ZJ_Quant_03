"""Tests for the zero-dependency data-layer logger (全链路日志方案 v2.0 §4)."""
from __future__ import annotations

import pytest

from quant_data import logging as ql


@pytest.fixture
def sink():
    """Capture records into a list; restore global logger state afterwards."""
    records: list[ql.LogRecord] = []
    ql.unregister_all_sinks()
    ql.register_sink(records.append)
    saved_threshold = ql.get_threshold()
    yield records
    ql.unregister_all_sinks()
    ql.set_threshold(saved_threshold)


def test_zero_external_dependencies():
    """The logging module must not drag in quant_web/sqlalchemy/fastapi (doc §2.2)."""
    import sys

    import quant_data.logging as mod  # noqa: F401

    # nothing the module imported should be a heavy web/orm dependency
    assert "quant_web" not in sys.modules
    src = mod.__file__
    assert src.endswith("logging.py")


def test_info_carries_extra(sink):
    ql.get_logger("quant_data.test").info("test.event", table="raw.bars_daily", n=5)
    assert len(sink) == 1
    rec = sink[0]
    assert rec.level == "INFO"
    assert rec.event == "test.event"
    assert rec.extra == {"table": "raw.bars_daily", "n": 5}


def test_debug_dropped_by_default(sink):
    ql.get_logger("t").debug("debug.event")
    assert sink == []


def test_set_threshold_enables_debug(sink):
    ql.set_threshold("DEBUG")
    ql.get_logger("t").debug("debug.event")
    assert len(sink) == 1 and sink[0].level == "DEBUG"


def test_context_bind_and_reset(sink):
    log = ql.get_logger("t")
    token = ql.bind_context(trace_id="t_abc", actor="weilin")
    log.info("with.ctx")
    ql.reset_context(token)
    log.info("without.ctx")
    assert sink[0].trace_id == "t_abc" and sink[0].actor == "weilin"
    assert sink[1].trace_id is None and sink[1].actor is None


def test_timed_emits_done_with_duration(sink):
    log = ql.get_logger("t")
    with ql.timed(log, "ingest.write", table="x") as m:
        m["row_count"] = 42
    assert len(sink) == 1
    rec = sink[0]
    assert rec.event == "ingest.write.done"
    assert rec.duration_ms is not None and rec.duration_ms >= 0
    assert rec.extra["row_count"] == 42 and rec.extra["table"] == "x"


def test_exception_captures_type_and_stack(sink):
    log = ql.get_logger("t")
    try:
        raise ValueError("boom")
    except ValueError:
        log.exception("op.failed", batch_id="b1")
    rec = sink[0]
    assert rec.level == "ERROR"
    assert rec.exc_type == "ValueError" and rec.exc_message == "boom"
    assert rec.stack and "ValueError" in rec.stack
    assert rec.extra == {"batch_id": "b1"}


def test_warn_has_no_stack_error_does(sink):
    log = ql.get_logger("t")
    log.warn("a.warn")
    log.error("a.error", exc=RuntimeError("x"))
    assert sink[0].stack is None
    assert sink[1].stack is not None


def test_sink_failure_is_isolated(sink):
    good: list = []

    def boom(_):
        raise RuntimeError("sink down")

    ql.register_sink(boom)
    ql.register_sink(good.append)
    # must not raise even though one sink throws
    ql.get_logger("t").info("still.works")
    assert any(r.event == "still.works" for r in good)


def test_slow_query_knob_roundtrip():
    ql.set_slow_query_ms(1234)
    assert ql.get_slow_query_ms() == 1234
    ql.set_slow_query_ms(2000)
