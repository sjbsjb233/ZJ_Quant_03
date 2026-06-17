"""JSONL → app_log backfill self-heal (可观测层重构方案 §8.3②).

When web-backend is down, each layer keeps writing its **local JSONL first** (本地
优先) — the data/factor services, and now the sentinel's own alerts (§5.4) — so a
crash window loses ship attempts but never the on-disk record. Once the backend
recovers, this task scans those JSONL files and re-inserts any record missing from
the central ``app_log``, deduplicated by ``(trace_id, ts, event, source)`` so the
ledger **self-heals** instead of leaving a permanent gap (the 2026-06-01 P0 blind
window).

Bounded by design: only records newer than ``since_hours`` are considered and a
hard ``limit`` caps a single run, so a recovery scan can never stall the process.
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import os

from ..db.models import AppLog

_COLS = ("level", "logger", "event", "source", "trace_id", "request_id", "session_id",
         "actor", "route", "method", "status_code", "duration_ms", "message",
         "extra", "exc_type", "exc_message", "stack", "host")


def _parse_ts(value) -> dt.datetime | None:
    # 全系统以 naive UTC 存储/比较 (datetime.utcnow)，所以 tz-aware 的输入必须先
    # 折算成 UTC 再去掉时区,而不是文本截断偏移量(那样会把本地墙钟当成 UTC,
    # 且只处理 "+" 偏移、丢掉 "-" 偏移的记录)。
    if isinstance(value, dt.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(dt.timezone.utc)
        return value.replace(tzinfo=None)
    if not isinstance(value, str) or not value:
        return None
    s = value.strip()
    # fromisoformat (Py3.11+) 接受尾随的 "Z" 与 ±HH:MM 偏移;去掉亚秒部分,
    # 但保留偏移文本以便正确折算。
    try:
        parsed = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(dt.timezone.utc)
    return parsed.replace(tzinfo=None)


def _key(ts: dt.datetime, source, event, trace_id, message) -> tuple:
    # second-resolution ts so a re-parsed JSONL line keys the same as its DB row.
    # message is in the key because monitor records carry no trace_id and reuse a
    # per-code event (monitor.<code>), so two distinct targets in the same second
    # would otherwise collide and the second be dropped as a false duplicate.
    return (ts.replace(microsecond=0), source or "", event or "", trace_id or "",
            (message or "")[:200])


def _iter_records(dirs: list[str]):
    seen_files: set[str] = set()
    for d in dirs:
        if not d or not os.path.isdir(d):
            continue
        for pattern in ("*.log", "*.jsonl"):
            for path in sorted(glob.glob(os.path.join(d, pattern))):
                if path in seen_files:
                    continue
                seen_files.add(path)
                try:
                    with open(path, encoding="utf-8") as fh:
                        for line in fh:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                yield json.loads(line)
                            except (ValueError, TypeError):
                                continue
                except OSError:
                    continue


def run_backfill(session_factory, dirs: list[str], *, since_hours: int = 48,
                 limit: int = 5000) -> dict:
    """Scan ``dirs`` for JSONL records, insert those missing from ``app_log``.

    Returns ``{scanned, inserted, skipped}``. Never raises — best-effort self-heal.
    """
    cutoff = dt.datetime.utcnow() - dt.timedelta(hours=max(1, since_hours))
    scanned = inserted = skipped = 0
    try:
        # snapshot existing keys in the window once, so we don't query per line
        with session_factory() as db:
            existing: set[tuple] = set()
            for ts, source, event, trace_id, message in (
                db.query(AppLog.ts, AppLog.source, AppLog.event, AppLog.trace_id, AppLog.message)
                  .filter(AppLog.ts >= cutoff).all()):
                if ts is not None:
                    existing.add(_key(ts, source, event, trace_id, message))

            batch: list[AppLog] = []
            for rec in _iter_records(dirs):
                if not isinstance(rec, dict):
                    continue
                ts = _parse_ts(rec.get("ts"))
                if ts is None or ts < cutoff:
                    continue
                scanned += 1
                k = _key(ts, rec.get("source"), rec.get("event"), rec.get("trace_id"),
                         rec.get("message"))
                if k in existing:
                    skipped += 1
                    continue
                existing.add(k)
                row = AppLog(ts=ts, **{c: rec.get(c) for c in _COLS})
                if not row.level:
                    row.level = "INFO"
                if not row.logger:
                    row.logger = "backfill"
                if not row.event:
                    row.event = "backfill.record"
                if not row.source:
                    row.source = "monitor"
                batch.append(row)
                inserted += 1
                if len(batch) >= limit:
                    break
            if batch:
                db.add_all(batch)
                db.commit()
    except Exception:  # noqa: BLE001 - self-heal must never break startup / an admin call
        pass
    return {"scanned": scanned, "inserted": inserted, "skipped": skipped}
