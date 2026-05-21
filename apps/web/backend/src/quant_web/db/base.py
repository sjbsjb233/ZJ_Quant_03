"""Engine + session factory for the audit/app database."""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from ..config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()
# job threads share the engine; SQLite needs cross-thread access + a busy timeout
_connect_args = (
    {"check_same_thread": False, "timeout": 30}
    if _settings.audit_db_url.startswith("sqlite")
    else {}
)
engine = create_engine(_settings.audit_db_url, connect_args=_connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db() -> None:
    """Create all tables (idempotent)."""
    from . import models  # noqa: F401 - register mappers

    Base.metadata.create_all(engine)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
