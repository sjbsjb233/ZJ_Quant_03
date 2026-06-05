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


# Columns added after the original schema shipped. SQLAlchemy's create_all()
# creates *new tables* (e.g. factor_run_degradation) but never ALTERs an
# existing one, so a factor.db created before the degradation feature needs
# these added in place. Idempotent: only adds what PRAGMA reports missing.
_ADDED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "factor_run": [
        ("degradation_level", "VARCHAR DEFAULT 'none'"),
        ("degradation_count", "INTEGER DEFAULT 0"),
        ("has_critical", "BOOLEAN DEFAULT 0"),
        ("preflight", "JSON"),
    ],
    "eval_summary": [
        ("degradation_level", "VARCHAR DEFAULT 'none'"),
        ("degradation_count", "INTEGER DEFAULT 0"),
        ("has_critical", "BOOLEAN DEFAULT 0"),
    ],
    # 因子篮子多级编排 (因子层重构 §4.2) — old DBs need these on `tasks`.
    "tasks": [
        ("brun_id", "VARCHAR"),
        ("seq_no", "INTEGER DEFAULT 0"),
        ("gate", "VARCHAR DEFAULT 'ready'"),
    ],
}


def _migrate_added_columns() -> bool:
    """ALTER TABLE ADD COLUMN for any post-launch column not yet present.

    SQLite only (the whole store is SQLite); a no-op on a brand-new DB because
    create_all already produced the full schema, but essential for upgrades.
    Returns True on success, False if the migration was skipped (non-SQLite
    backend / locked db).
    """
    insp_ok = True
    try:
        with engine.begin() as conn:
            existing_tables = {
                row[0]
                for row in conn.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            for table, cols in _ADDED_COLUMNS.items():
                if table not in existing_tables:
                    continue  # create_all will/already built it with all columns
                have = {
                    row[1]
                    for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")
                }
                for name, ddl in cols:
                    if name not in have:
                        conn.exec_driver_sql(
                            f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"
                        )
    except Exception:  # noqa: BLE001 — non-SQLite backends / locked db: skip quietly
        insp_ok = False
    return insp_ok


def init_db() -> None:
    from . import models  # noqa: F401  (ensure tables are imported)

    Base.metadata.create_all(engine)
    _migrate_added_columns()


def rebind(url: str) -> None:
    """Test helper — point the engine at a different URL (e.g. tmp file)."""
    global engine, SessionLocal
    engine = _make_engine(url)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
