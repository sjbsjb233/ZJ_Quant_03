"""SQLAlchemy engine + session factory for keeper.db (Job / scheduler ledger).

WAL + busy_timeout (like factor.db) so the request thread, the JobRunner pool
threads, and the scheduler thread can all touch the file safely. keeper.db is
data-keeper's private state on its own volume — never shared cross-container.
"""
from __future__ import annotations

import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from ..config import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine(url: str | None = None):
    url = url or get_settings().keeper_db_url
    connect_args = {}
    if url.startswith("sqlite:///"):
        connect_args = {"timeout": 30, "check_same_thread": False}
        path = url.replace("sqlite:///", "", 1)
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
    eng = create_engine(url, connect_args=connect_args, pool_pre_ping=True, future=True)

    @event.listens_for(eng, "connect")
    def _pragma(conn, _):  # noqa: ANN001
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("PRAGMA busy_timeout=30000;")
            cur.execute("PRAGMA synchronous=NORMAL;")
            cur.close()
        except Exception:  # noqa: BLE001
            pass

    return eng


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    from . import models  # noqa: F401 - register mappers

    Base.metadata.create_all(engine)


def rebind(url: str) -> None:
    """Test helper — point the engine at a different URL (e.g. tmp file)."""
    global engine, SessionLocal
    engine = _make_engine(url)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
