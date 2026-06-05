"""Memory-aware admission gate (修复 worker OOM crash-loop).

A factor evaluation scans the full bars_daily parquet set into polars and peaks
at multiple GB of RSS. Running FACTOR_CONCURRENCY of them as threads in one
process OOM-killed the worker (silent SIGKILL → Docker restart → re-claim →
crash-loop, surfacing as "worker lost (stale heartbeat)" and a failed basket
run). The fix caps concurrent *heavy* (data-reading) tasks via
``claim_next_filtered(..., allow_heavy=False)``; this pins that behaviour.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture()
def db_setup(tmp_path):
    os.environ["FACTOR_STORE_ROOT"] = str(tmp_path)
    os.environ["FACTOR_DB_URL"] = f"sqlite:///{tmp_path}/factor.db"
    os.environ["JWT_SECRET"] = "test-secret"
    os.environ["QUANT_DATA_ROOT"] = "./data-test"
    os.environ["FACTOR_ROLE"] = "worker"
    os.environ["PREFLIGHT_ENABLED"] = "false"

    from factor_service.config import reset_settings_cache
    reset_settings_cache()
    from factor_service.db import base as db_base
    db_base.rebind(os.environ["FACTOR_DB_URL"])
    db_base.init_db()
    yield db_base


def test_heavy_types_cover_data_readers():
    """The gate must cover every data-reading task type dispatched by the worker."""
    from factor_service.queue import store

    assert set(store.HEAVY_TASK_TYPES) == {
        "evaluation",
        "correlation",
        "basket_eval",
        "basket_corr",
    }


def test_gate_skips_heavy_but_claims_light(db_setup):
    """With allow_heavy=False a queued heavy task is skipped in favour of a light
    one — even though the heavy task is older (would otherwise win on FIFO)."""
    from factor_service.queue import store

    with db_setup.SessionLocal() as db:
        # Heavy task enqueued FIRST → older created_at → normally claimed first.
        heavy = store.enqueue(db, task_type="basket_eval", payload={"x": 1})
        light = store.enqueue(db, task_type="import_validate", payload={"y": 2})

        # Gate closed → the heavy one is invisible, the light one is claimed.
        t = store.claim_next_filtered(db, "w1", allow_routine=True, allow_heavy=False)
        assert t is not None
        assert t.task_id == light.task_id
        # claim commits on a separate connection; expire the identity-map copy.
        db.refresh(t)
        assert t.status == "running"

        # The heavy task is still queued, waiting for a heavy slot.
        db.refresh(heavy)
        assert heavy.status == "queued"


def test_gate_open_claims_heavy_first(db_setup):
    """With allow_heavy=True the older heavy task is claimed normally (FIFO)."""
    from factor_service.queue import store

    with db_setup.SessionLocal() as db:
        heavy = store.enqueue(db, task_type="evaluation", payload={"x": 1})
        store.enqueue(db, task_type="import_validate", payload={"y": 2})

        t = store.claim_next_filtered(db, "w1", allow_routine=True, allow_heavy=True)
        assert t is not None
        assert t.task_id == heavy.task_id


def test_gate_closed_with_only_heavy_returns_none(db_setup):
    """When only heavy tasks are queued and the gate is closed, nothing is
    claimed — the dispatcher leaves them for a later cycle rather than OOM."""
    from factor_service.queue import store

    with db_setup.SessionLocal() as db:
        store.enqueue(db, task_type="basket_eval", payload={"x": 1})
        store.enqueue(db, task_type="correlation", payload={"x": 2})

        t = store.claim_next_filtered(db, "w1", allow_routine=True, allow_heavy=False)
        assert t is None


def test_gate_filters_heavy_in_routine_lane(db_setup):
    """The heavy filter applies to the routine lane too (tracking ticks ride
    routine): a routine heavy task is skipped while a routine light one is
    claimed when the gate is closed."""
    from factor_service.queue import store

    with db_setup.SessionLocal() as db:
        heavy = store.enqueue(db, task_type="basket_eval", payload={"x": 1}, lane="routine")
        light = store.enqueue(db, task_type="import_validate", payload={"y": 2}, lane="routine")

        t = store.claim_next_filtered(db, "w1", allow_routine=True, allow_heavy=False)
        assert t is not None
        assert t.task_id == light.task_id
        db.refresh(heavy)
        assert heavy.status == "queued"
