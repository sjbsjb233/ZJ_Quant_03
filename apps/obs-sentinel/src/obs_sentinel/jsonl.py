"""Local JSONL fallback for the sentinel's own alerts (可观测层重构方案 §5.4 / §8.3).

The legacy sentinel shipped alerts straight to web-backend and **dropped them when
the backend was down** — the exact failure mode of the 2026-06-01 P0. We fix that
by mirroring the other services' "本地优先" discipline: every monitor record is
appended to a local JSONL file *first*, then shipped. POST failures lose only the
ship, never the on-disk record, so a JSONL→app_log backfill task can heal the
central store once the backend recovers (§8.3②).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading


class JsonlWriter:
    def __init__(self, directory: str, filename: str = "monitor.jsonl") -> None:
        self._dir = directory
        self._path = os.path.join(directory, filename)
        self._lock = threading.Lock()
        self._ok: bool | None = None

    @property
    def ok(self) -> bool:
        return bool(self._ok)

    @property
    def path(self) -> str:
        return self._path

    def write(self, record: dict) -> bool:
        """Append one record (adds ``ts`` if absent). Returns True on success.

        Never raises — a read-only / missing volume must not take down the loop.
        """
        record = {"ts": dt.datetime.utcnow().isoformat() + "Z", **record}
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            try:
                os.makedirs(self._dir, exist_ok=True)
                with open(self._path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                self._ok = True
                return True
            except OSError:
                self._ok = False
                return False
