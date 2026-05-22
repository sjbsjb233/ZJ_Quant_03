"""Shared read-modify-write helpers for the mutable metadata tables.

Metadata files are tiny mutable single-file Parquet tables updated in place, so
concurrent ingests would otherwise race on read-modify-write. ``metadata_lock``
serialises every mutation: a re-entrant in-process RLock (so nested calls like
update_source_priority -> update_source don't deadlock) plus a cross-process
``flock`` taken only at the outermost level.
"""
from __future__ import annotations

import contextlib
import functools
import threading
import time

from .. import config, storage
from ..logging import get_logger
from ..schemas import TableSpec

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None

_RLOCK = threading.RLock()
_local = threading.local()
_log = get_logger("quant_data.metadata")


@contextlib.contextmanager
def metadata_lock(root=None):
    with _RLOCK:
        depth = getattr(_local, "depth", 0)
        _local.depth = depth + 1
        fh = None
        try:
            if fcntl is not None and depth == 0:
                base = config.data_root(root) / "metadata"
                base.mkdir(parents=True, exist_ok=True)
                fh = open(base / ".lock", "w")
                fcntl.flock(fh, fcntl.LOCK_EX)
            yield
        finally:
            if fh is not None:
                fcntl.flock(fh, fcntl.LOCK_UN)
                fh.close()
            _local.depth -= 1


def locked(fn):
    """Decorator: run a mutating metadata op under metadata_lock(root).

    Also emits a ``metadata.<ledger>.write`` DEBUG log (doc §4.2.4) carrying the
    function name and the wall time spent holding the lock, so every mutation of
    a ledger is observable without hand-writing a log call in each function.
    """
    ledger = fn.__module__.rsplit(".", 1)[-1]

    @functools.wraps(fn)
    def wrap(*args, **kwargs):
        with metadata_lock(kwargs.get("root")):
            # measure inside the lock so lock_held_ms is hold time, not wait time
            start = time.perf_counter()
            result = fn(*args, **kwargs)
            held_ms = int((time.perf_counter() - start) * 1000)
        _log.debug(f"metadata.{ledger}.write", op=fn.__name__, lock_held_ms=held_ms)
        return result

    return wrap


def load(spec: TableSpec, root=None) -> list[dict]:
    return storage.read_rows(spec, root)


def save(spec: TableSpec, rows: list[dict], root=None) -> None:
    storage.write_rows(spec, rows, root)


def pk_of(spec: TableSpec, row: dict) -> tuple:
    return tuple(row[k] for k in spec.primary_key)


def get(spec: TableSpec, rows: list[dict], key: tuple) -> dict | None:
    for r in rows:
        if pk_of(spec, r) == key:
            return r
    return None


def require(spec: TableSpec, rows: list[dict], key: tuple) -> dict:
    row = get(spec, rows, key)
    if row is None:
        raise KeyError(f"{spec.name}: no row with key {key}")
    return row


def upsert(spec: TableSpec, rows: list[dict], row: dict) -> list[dict]:
    key = pk_of(spec, row)
    for i, r in enumerate(rows):
        if pk_of(spec, r) == key:
            rows[i] = row
            return rows
    rows.append(row)
    return rows


def blank(spec: TableSpec) -> dict:
    """A row dict with every column present and defaulted to None."""
    return {name: None for name in spec.all_names()}
