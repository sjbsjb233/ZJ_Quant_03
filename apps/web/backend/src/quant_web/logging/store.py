"""AppLogStore -- the async write path for app_log (全链路日志方案 v2.0 §7.2/§7.3).

A producer (request thread / data-layer sink / frontend route) calls
:meth:`enqueue`; that returns immediately after redacting the payload, pushing it
onto an in-memory queue, and broadcasting to the live SSE stream. A single
background daemon thread drains the queue in batches and writes to:

  * the ``app_log`` table (``bulk_insert_mappings``), and
  * a daily JSON Lines file under ``log_file_dir`` (for ops / external shippers).

Design rules honoured here:
  * **non-blocking**: request threads only ``put`` (doc §G5, ≤5% overhead);
  * **back-pressure**: a full queue drops DEBUG/INFO but keeps WARN+ and
    self-reports a single ``log.queue.dropped`` CRITICAL (doc §7.2);
  * **degrade safely**: an unwritable ``log_file_dir`` disables the file sink and
    self-reports, never crashing the app (doc §5.7);
  * **deterministic flush**: :meth:`flush` blocks until the queue is drained, so
    shutdown and tests can read what they just wrote.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import queue
import socket
import sys
import threading
import time
from typing import TYPE_CHECKING

from ..db.models import AppLog
from . import sse
from .alerts import Alerter
from .redact import redact

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.orm import sessionmaker

    from ..config import Settings

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40, "CRITICAL": 50}
_HOST = socket.gethostname()[:64]

# AppLog columns we map onto explicitly; everything else stays inside `extra`.
_COLUMNS = (
    "ts", "level", "logger", "event", "source", "trace_id", "request_id",
    "session_id", "actor", "route", "method", "status_code", "duration_ms",
    "message", "extra", "exc_type", "exc_message", "stack", "host",
)


class AppLogStore:
    def __init__(
        self,
        session_factory: "sessionmaker",
        settings: "Settings",
        *,
        queue_max: int = 10_000,
        batch_size: int = 100,
        flush_interval: float = 1.0,
    ) -> None:
        self._sf = session_factory
        self._settings = settings
        self._sensitive = settings.sensitive_keys
        self._q: queue.Queue = queue.Queue(maxsize=queue_max)
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._dropped = 0
        self._dropped_reported = False
        self.db_ok = bool(settings.log_to_db)
        # DB 写入可能因瞬态故障失败(SQLite "database is locked"、连接抖动、
        # 单行约束/序列化错误)。不要一次失败就永久关闭 DB sink:连续失败累计
        # 到阈值才降级,且每次成功写入即清零;降级后下一批仍会重试(re-probe),
        # 一旦成功就自动恢复 db_ok=True。
        self._db_fail_streak = 0
        self._db_fail_threshold = 5
        self._alerter = Alerter(
            getattr(settings, "log_alert_webhook", ""),
            getattr(settings, "log_alert_min_level", "ERROR"),
            getattr(settings, "log_alert_rate_per_min", 20),
        )
        # --- file sink state ---
        # 文件句柄/字节计数被后台 flusher 线程与 producer 线程(队列溢出快路径
        # _handle_overflow -> _write_file)同时改写,必须用锁串行化文件操作,
        # 否则在背压时会出现句柄重复关闭、轮转错乱、JSONL 行交错。
        self._file_lock = threading.Lock()
        self._file_enabled = bool(settings.log_to_file)
        self._fh = None
        self._fh_date: str | None = None
        self._fh_bytes = 0
        self.file_ok = False
        if self._file_enabled:
            self._init_file_sink()

    # -- file sink --------------------------------------------------------
    def _init_file_sink(self) -> None:
        try:
            os.makedirs(self._settings.log_file_dir, exist_ok=True)
            # probe writability
            probe = os.path.join(self._settings.log_file_dir, ".write_probe")
            with open(probe, "w") as fh:
                fh.write("ok")
            os.remove(probe)
            self.file_ok = True
        except OSError as exc:
            self._file_enabled = False
            self.file_ok = False
            sys.stderr.write(
                f"[app-log] CRITICAL log.file_sink.unavailable dir={self._settings.log_file_dir} "
                f"err={exc}; degrading to DB-only sink\n"
            )

    def _file_for_today(self):
        today = dt.date.today().isoformat()
        if self._fh is not None and self._fh_date == today:
            return self._fh
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
        path = os.path.join(self._settings.log_file_dir, f"quant_web-{today}.log")
        self._fh = open(path, "a", encoding="utf-8")
        self._fh_date = today
        self._fh_bytes = self._fh.tell()
        return self._fh

    def _rotate_if_needed(self) -> None:
        max_bytes = self._settings.log_file_max_mb * 1024 * 1024
        if self._fh is None or self._fh_bytes < max_bytes:
            return
        self._fh.close()
        self._fh = None
        base = os.path.join(self._settings.log_file_dir, f"quant_web-{self._fh_date}")
        # shift .N backups, then move the live file to .1
        for n in range(self._settings.log_file_backup_count - 1, 0, -1):
            src, dst = f"{base}.{n}.log", f"{base}.{n + 1}.log"
            if os.path.exists(src):
                os.replace(src, dst)
        if os.path.exists(f"{base}.log"):
            os.replace(f"{base}.log", f"{base}.1.log")
        self._fh_bytes = 0
        self._fh_date = None  # force reopen

    def _write_file(self, rows: list[dict]) -> None:
        if not self._file_enabled:
            return
        # 整个写入(含 open/rotate/close)持锁,保证 flusher 线程与溢出快路径
        # 的 producer 线程不会交错操作同一个文件句柄。
        with self._file_lock:
            try:
                fh = self._file_for_today()
                for row in rows:
                    line = json.dumps(row, default=str, ensure_ascii=False) + "\n"
                    fh.write(line)
                    self._fh_bytes += len(line.encode("utf-8"))
                fh.flush()
                self._rotate_if_needed()
            except OSError as exc:  # pragma: no cover - disk full / perms changed at runtime
                self.file_ok = False
                sys.stderr.write(f"[app-log] file sink write failed: {exc}\n")

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="app-log-flush", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        self.flush()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._alerter.stop()
        with self._file_lock:
            if self._fh is not None:
                try:
                    self._fh.close()
                except OSError:
                    pass

    def flush(self) -> None:
        """Block until every queued record has been written (tests + shutdown)."""
        if self._thread is not None and self._thread.is_alive():
            self._q.join()
        else:  # no worker running -> drain inline
            self._drain_inline()

    # -- enqueue (producer side) -----------------------------------------
    def enqueue(self, record: dict) -> None:
        row = self._to_row(record)
        # redaction + SSE happen on the producer thread so the live stream and
        # what we persist are identical, and never leak secrets.
        if row.get("extra") is not None:
            row["extra"] = redact(row["extra"], self._sensitive)
        sse.broadcast(row)
        try:
            self._q.put_nowait(row)
        except queue.Full:
            self._handle_overflow(row)

    def _handle_overflow(self, row: dict) -> None:
        level = _LEVELS.get(row.get("level", "INFO"), 20)
        self._dropped += 1
        if level >= _LEVELS["WARN"]:
            # keep important records: write straight to the file sink, bypassing
            # the saturated queue, so a WARN/ERROR is never silently lost.
            self._write_file([row])
        if not self._dropped_reported:
            self._dropped_reported = True
            crit = self._to_row({
                "level": "CRITICAL", "logger": "quant_web.logging", "source": "backend",
                "event": "log.queue.dropped",
                "message": f"app_log queue saturated; dropping low-priority records "
                           f"(dropped so far={self._dropped})",
                "extra": {"queue_max": self._q.maxsize},
            })
            self._write_file([crit])
            sse.broadcast(crit)

    # -- background flusher ----------------------------------------------
    def _run(self) -> None:
        while not self._stopping.is_set():
            batch = self._collect_batch()
            if batch:
                self._write_batch(batch)
        # final drain after stop signalled
        remaining = self._collect_batch(block=False)
        if remaining:
            self._write_batch(remaining)

    def _collect_batch(self, block: bool = True) -> list[dict]:
        batch: list[dict] = []
        if block:
            try:
                batch.append(self._q.get(timeout=self._flush_interval))
            except queue.Empty:
                return batch
        while len(batch) < self._batch_size:
            try:
                batch.append(self._q.get_nowait())
            except queue.Empty:
                break
        return batch

    def _write_batch(self, batch: list[dict]) -> None:
        # 三个 sink 相互隔离:任何一个失败都不能拖垮其余两个,也不能让 worker 崩溃。
        try:
            self._write_file(batch)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[app-log] file sink failed: {exc}\n")
        try:
            self._try_write_db(batch)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[app-log] db sink failed: {exc}\n")
        try:
            self._alerter.feed(batch)   # opt-in ERROR+ webhook (no-op if unconfigured)
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"[app-log] alerter failed: {exc}\n")
        for _ in batch:
            self._q.task_done()

    def _try_write_db(self, batch: list[dict]) -> None:
        """写 DB 并维护瞬态故障的 streak/re-probe 状态。

        即使 db_ok 当前为 False 也会尝试(re-probe):瞬态故障恢复后自动
        把 db_ok 置回 True;只有连续失败累计到阈值才关闭 sink。"""
        if not bool(self._settings.log_to_db):
            return
        try:
            self._write_db(batch)
        except Exception as exc:  # noqa: BLE001 - transient DB hiccup, don't latch off
            self._db_fail_streak += 1
            sys.stderr.write(
                f"[app-log] db write failed ({self._db_fail_streak}/{self._db_fail_threshold}): {exc}\n"
            )
            if self._db_fail_streak >= self._db_fail_threshold:
                self.db_ok = False
            return
        # 成功:清零 streak 并恢复 sink
        self._db_fail_streak = 0
        if not self.db_ok:
            self.db_ok = True
            sys.stderr.write("[app-log] db sink recovered; resuming DB writes\n")

    def _drain_inline(self) -> None:
        batch: list[dict] = []
        while True:
            try:
                batch.append(self._q.get_nowait())
            except queue.Empty:
                break
        if batch:
            try:
                self._write_file(batch)
            except Exception as exc:  # noqa: BLE001
                sys.stderr.write(f"[app-log] file sink failed: {exc}\n")
            try:
                self._try_write_db(batch)
            except Exception as exc:  # noqa: BLE001
                sys.stderr.write(f"[app-log] db sink failed: {exc}\n")
            for _ in batch:
                self._q.task_done()

    def _write_db(self, batch: list[dict]) -> None:
        mappings = [{k: row.get(k) for k in _COLUMNS} for row in batch]
        with self._sf() as db:
            db.bulk_insert_mappings(AppLog, mappings)
            db.commit()

    # -- normalisation ----------------------------------------------------
    def _to_row(self, record: dict) -> dict:
        ts = record.get("ts")
        row = {
            "ts": _coerce_ts(ts),
            "level": record.get("level", "INFO"),
            "logger": record.get("logger", "unknown"),
            "event": record.get("event", ""),
            "source": record.get("source", "backend"),
            "trace_id": record.get("trace_id"),
            "request_id": record.get("request_id"),
            "session_id": record.get("session_id"),
            "actor": record.get("actor"),
            "route": record.get("route"),
            "method": record.get("method"),
            "status_code": record.get("status_code"),
            "duration_ms": record.get("duration_ms"),
            "message": record.get("message"),
            "extra": record.get("extra"),
            "exc_type": record.get("exc_type"),
            "exc_message": record.get("exc_message"),
            "stack": record.get("stack"),
            "host": record.get("host") or _HOST,
        }
        return row

    # -- introspection / maintenance -------------------------------------
    def health(self) -> dict:
        return {
            "db_sink": "ok" if self.db_ok else "off",
            "file_sink": "ok" if self.file_ok else "off",
            "queue_size": self._q.qsize(),
            "queue_max": self._q.maxsize,
            "dropped": self._dropped,
            "sse_clients": sse.hub.client_count,
        }

    def archive_old(self, retention_days: int | None = None) -> dict:
        """Roll app_log rows older than the window into gzip JSON Lines, then
        delete them from the DB (doc §7.5). Returns a small summary dict."""
        retention_days = retention_days or self._settings.log_db_retention_days
        cutoff = dt.datetime.utcnow() - dt.timedelta(days=retention_days)
        archive_dir = os.path.join(self._settings.log_file_dir, "archive")
        written = 0
        by_day: dict[str, list[dict]] = {}
        with self._sf() as db:
            rows = db.query(AppLog).filter(AppLog.ts < cutoff).order_by(AppLog.ts).all()
            for r in rows:
                day = (r.ts or cutoff).date().isoformat()
                by_day.setdefault(day, []).append(_applog_to_jsonable(r))
            if by_day and self.file_ok:
                for day, recs in by_day.items():
                    month_dir = os.path.join(archive_dir, day[:7])
                    os.makedirs(month_dir, exist_ok=True)
                    path = os.path.join(month_dir, f"app_log-{day}.jsonl.gz")
                    with gzip.open(path, "at", encoding="utf-8") as gz:
                        for rec in recs:
                            gz.write(json.dumps(rec, default=str, ensure_ascii=False) + "\n")
                            written += 1
            # Only delete what was actually archived (or when there was nothing to
            # archive). If rows exist but the archive sink failed, KEEP the rows --
            # never delete un-archived logs (avoids silent data loss).
            deleted = False
            if written or not by_day:
                db.query(AppLog).filter(AppLog.ts < cutoff).delete(synchronize_session=False)
                db.commit()
                deleted = True
        result = {"archived": written, "cutoff": cutoff.isoformat(), "days": len(by_day),
                  "deleted": deleted}
        if by_day and not written:
            result["note"] = "archive sink unavailable; rows kept (not deleted)"
        return result


def _coerce_ts(ts) -> dt.datetime:
    if isinstance(ts, dt.datetime):
        return ts.replace(tzinfo=None)
    if isinstance(ts, str):
        try:
            s = ts.rstrip("Z").replace("T", " ")
            return dt.datetime.fromisoformat(s)
        except ValueError:
            pass
    return dt.datetime.utcnow()


def _applog_to_jsonable(r: "AppLog") -> dict:
    return {c: getattr(r, c) for c in (
        "id", "ts", "level", "logger", "event", "source", "trace_id", "request_id",
        "session_id", "actor", "route", "method", "status_code", "duration_ms",
        "message", "extra", "exc_type", "exc_message", "stack", "host",
    )}


# --- module singleton -------------------------------------------------------
_store: AppLogStore | None = None


def init_store(session_factory: "sessionmaker", settings: "Settings", **kw) -> AppLogStore:
    global _store
    _store = AppLogStore(session_factory, settings, **kw)
    _store.start()
    return _store


def get_store() -> AppLogStore | None:
    return _store


def set_store(store: AppLogStore | None) -> None:
    global _store
    _store = store
