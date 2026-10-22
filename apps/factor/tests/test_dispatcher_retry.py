"""Regression: 自动重试必须清掉上一轮的 cancel_requested。

若用户先请求过取消 (cancel_requested=True),worker 还没看到就因无关异常失败,
重试若保留该标记会被 worker 的 cancelled() 回调一上来判为 canceled —— 用户的
瞬时失败永远拿不到真正的重试 (dispatcher._finalize_future)。
"""
from __future__ import annotations

import os
from concurrent.futures import Future

import pytest


@pytest.fixture(scope="module")
def db_base(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("disp-store")
    os.environ["FACTOR_STORE_ROOT"] = str(tmp)
    os.environ["FACTOR_DB_URL"] = f"sqlite:///{tmp}/factor.db"
    os.environ["JWT_SECRET"] = "test-secret"
    os.environ["FACTOR_ROLE"] = "api"

    from factor_service.config import reset_settings_cache
    reset_settings_cache()
    from factor_service.db import base as _db_base
    _db_base.rebind(os.environ["FACTOR_DB_URL"])
    _db_base.init_db()
    return _db_base


def test_auto_retry_clears_cancel_requested(db_base):
    from factor_service.queue import dispatcher, store

    with db_base.SessionLocal() as db:
        t = store.enqueue(db, task_type="factor.eval.daily", payload={}, max_attempts=2)
        tid = t.task_id
        t.cancel_requested = True   # user asked to cancel...
        t.attempt = 0
        db.commit()

        fut: Future = Future()
        fut.set_exception(RuntimeError("boom"))   # ...then an unrelated failure
        dispatcher._finalize_future(db, tid, fut)

        db.refresh(t)
        assert t.status == "queued"          # retried (attempt < max_attempts)
        assert t.cancel_requested is False   # ← the fix: stale cancel flag cleared
        assert t.error is None


def test_terminal_failure_when_attempts_exhausted(db_base):
    from factor_service.queue import dispatcher, store

    with db_base.SessionLocal() as db:
        t = store.enqueue(db, task_type="factor.eval.daily", payload={}, max_attempts=1)
        tid = t.task_id
        t.attempt = 1   # the only attempt is already spent
        db.commit()

        fut: Future = Future()
        fut.set_exception(RuntimeError("boom"))
        dispatcher._finalize_future(db, tid, fut)

        db.refresh(t)
        assert t.status == "failed"
        assert "boom" in (t.error or "")
