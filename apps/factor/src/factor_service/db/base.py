"""SQLAlchemy engine + session factory for factor.db.

WAL mode + busy_timeout makes the file safe for two processes (api + worker)
on the same host. SQLite journal is created next to the file the first time
PRAGMA journal_mode runs.
"""
from __future__ import annotations

import os

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def _make_engine(url: str | None = None):
    url = url or os.environ.get(
        "FACTOR_DB_URL", "sqlite:///./factor-store/factor.db"
    )
    # SQLite-specific: same connection used across threads (uvicorn workers
    # are sync handlers running in a threadpool, so this is fine).
    connect_args = {}
    if url.startswith("sqlite:///"):
        connect_args = {"timeout": 30, "check_same_thread": False}
        # Make sure parent dir exists before creating the file.
        path = url.replace("sqlite:///", "", 1)
        # absolute (4 slashes) -> path starts with '/'; relative -> 3 slashes
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
    eng = create_engine(url, connect_args=connect_args, pool_pre_ping=True)

    @event.listens_for(eng, "connect")
    def _pragma(conn, _):
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("PRAGMA busy_timeout=30000;")
            cur.execute("PRAGMA synchronous=NORMAL;")
            cur.execute("PRAGMA foreign_keys=ON;")
            cur.close()
        except Exception:  # noqa: BLE001
            pass

    return eng


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    from . import models  # noqa: F401  (ensure tables are imported)

    Base.metadata.create_all(engine)


def rebind(url: str) -> None:
    """Test helper — point the engine at a different URL (e.g. tmp file)."""
    global engine, SessionLocal
    engine = _make_engine(url)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
