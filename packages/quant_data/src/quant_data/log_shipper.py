"""Out-of-process log shipper (容器化重构 V3 §6.7 -- 本地发射 + 中枢聚合).

When ``quant_data`` runs *inside its own container* (data-keeper, factor-*),
the in-process ``register_sink`` route into ``quant_web``'s ``AppLogStore`` no
longer exists -- they are different processes. This module bridges that gap with
**zero external dependencies** (stdlib only, like :mod:`quant_data.logging`):

  1. **local first** -- every record is appended to a daily JSON Lines file under
     ``jsonl_dir`` *before* anything else. If the central web-backend is down the
     line is still on disk (取证 / 前向兼容 Loki). This never blocks.
  2. **side-channel ship** -- a bounded background queue is drained in batches and
     POSTed to web-backend's ``/api/v1/logs/ingest`` (same async write path,
     SSE + trace join). When the queue is full we drop DEBUG/INFO but keep WARN+
     (synchronously file-backed), mirroring ``AppLogStore._handle_overflow``.
  3. **never break business code** -- a failed POST退避重试; a totally unreachable
     web-backend只是丢上报, the local JSONL一条不丢. No exception ever escapes.

Usage (data-keeper / factor ``obs.py``)::

    from quant_data import logging as qd_logging
    from quant_data.log_shipper import HttpLogShipper

    shipper = HttpLogShipper(url=..., token=..., source="data", jsonl_dir=...)
    qd_logging.register_sink(shipper.sink)      # data-layer LogRecords -> shipped
    # optionally bridge stdlib logging too (factor app logs): shipper.ship_row(...)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import queue
import socket
import threading
import time
import urllib.request

__all__ = ["HttpLogShipper"]

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
_HOST = socket.gethostname()[:64]


class HttpLogShipper:
    """Ship structured log rows to web-backend ``/logs/ingest`` (本地优先, 非阻塞)."""

    def __init__(
        self,
        url: str,
        token: str,
        *,
        source: str,
        jsonl_dir: str,
        batch: int = 50,
        flush_interval: float = 1.0,
        queue_max: int = 10_000,
        post_timeout: float = 5.0,
    ) -> None:
        self.url = url
        self.token = token
        self.source = source
        self.jsonl_dir = jsonl_dir
        self.batch = max(1, int(batch))
        self.flush_interval = max(0.05, float(flush_interval))
        self.post_timeout = post_timeout
        self._q: queue.Queue = queue.Queue(maxsize=queue_max)
        # 溢出时 WARN+ 不能在生产者线程里同步 POST(会阻塞业务线程数秒),
        # 改投这个小优先队列, 由后台 flusher 一并排出; 满了就丢最旧的一条。
        self._pq: queue.Queue = queue.Queue(maxsize=max(1, queue_max // 100))
        self._stopping = threading.Event()
        self._drain_deadline: float = 0.0  # stop() 设定的关停排队截止 (monotonic)
        self._file_ok = True
        try:
            os.makedirs(jsonl_dir, exist_ok=True)
        except OSError:
            self._file_ok = False
        self._thread = threading.Thread(target=self._run, name="log-shipper", daemon=True)
        self._thread.start()

    # -- sinks (producer side) ------------------------------------------------
    def sink(self, record) -> None:
        """Adapter for :func:`quant_data.logging.register_sink`.

        Converts a data-layer ``LogRecord`` into a row, stamps source + host, and
        ships it. Library logs are always the data layer's, so ``source`` is the
        shipper's configured source (``data`` for keeper; ``factor`` for factor
        per V3 §6.6 -- the ``host`` column still distinguishes the container).
        """
        try:
            row = record.as_dict()
        except Exception:  # noqa: BLE001 - a bad record must never break the caller
            return
        self.ship_row(row)

    def ship_row(self, row: dict) -> None:
        """Ship a pre-built row dict (used by the stdlib-logging bridge too)."""
        if not isinstance(row, dict):
            return
        row.setdefault("source", self.source)
        row.setdefault("host", _HOST)
        self._write_local(row)            # 1) local first — never lost
        try:
            self._q.put_nowait(row)       # 2) side-channel ship
        except queue.Full:
            # keep WARN+: 投入优先队列让后台线程排出, 绝不在生产者线程同步 POST
            # (本地 JSONL 已留底, 同步网络重试会冻结业务线程数秒)。
            if _LEVELS.get(str(row.get("level", "INFO")).upper(), 20) >= 30:
                while True:
                    try:
                        self._pq.put_nowait(row)
                        break
                    except queue.Full:
                        # 优先队列也满了: 丢最旧的一条腾位 (JSONL 仍留底)。
                        try:
                            self._pq.get_nowait()
                        except queue.Empty:
                            break

    # -- local JSONL fallback -------------------------------------------------
    def _write_local(self, row: dict) -> None:
        if not self._file_ok:
            return
        # 文件名按 UTC 日期, 与每条记录的 ts (UTC) 对齐 — 全系统统一 UTC,
        # 用本地日期会让同一 UTC 日的记录跨午夜分散到两个文件。
        utc_date = dt.datetime.now(dt.timezone.utc).date().isoformat()
        path = os.path.join(self.jsonl_dir, f"{self.source}-{utc_date}.log")
        try:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, default=str, ensure_ascii=False) + "\n")
        except OSError:
            # disk full / perms changed — degrade silently, keep shipping.
            self._file_ok = False

    # -- background flusher ---------------------------------------------------
    def _run(self) -> None:
        while not self._stopping.is_set():
            batch = self._collect()
            if batch:
                self._post(batch)
        # final drain on shutdown: 循环排空整个队列 (单批只取 self.batch 条会丢尾巴),
        # 用 _drain_deadline 兜底以尊重 stop(timeout=...)。
        while True:
            if self._drain_deadline and time.monotonic() >= self._drain_deadline:
                break
            tail = self._collect(block=False)
            if not tail:
                break
            self._post(tail)

    def _collect(self, block: bool = True) -> list[dict]:
        batch: list[dict] = []
        # 先排空优先队列 (溢出时改投的 WARN+), 再排主队列。
        while len(batch) < self.batch:
            try:
                batch.append(self._pq.get_nowait())
            except queue.Empty:
                break
        if block and not batch:
            try:
                batch.append(self._q.get(timeout=self.flush_interval))
            except queue.Empty:
                return batch
        while len(batch) < self.batch:
            try:
                batch.append(self._q.get_nowait())
            except queue.Empty:
                break
        return batch

    def _post(self, rows: list[dict], _retries: int = 3) -> None:
        if not rows or not self.url:
            return
        body = json.dumps({"records": rows}, default=str, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "X-Log-Token": self.token},
        )
        for i in range(_retries):
            try:
                urllib.request.urlopen(req, timeout=self.post_timeout).read()
                return
            except Exception:  # noqa: BLE001 - web unreachable: back off, local已留底
                if self._stopping.is_set():
                    return
                time.sleep(min(2 ** i, 5))

    # -- lifecycle ------------------------------------------------------------
    def stop(self, timeout: float = 5.0) -> None:
        self._drain_deadline = time.monotonic() + max(0.0, float(timeout))
        self._stopping.set()
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)
