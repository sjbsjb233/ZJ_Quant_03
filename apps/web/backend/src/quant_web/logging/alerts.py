"""ERROR/CRITICAL alert webhook (全链路日志方案 v2.0 §10 第4期 / §9.1).

Strictly opt-in: with no ``log_alert_webhook`` configured the Alerter starts no
thread and every method is a no-op, so default deployments and tests are
unaffected. When a URL is set, ERROR+ records are POSTed (fire-and-forget, on a
background thread, rate-limited) in the 钉钉/企业微信 robot text format. Failures
are swallowed — alerting must never break logging.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import urllib.request
from collections import deque

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40, "CRITICAL": 50}


class Alerter:
    def __init__(self, webhook_url: str, min_level: str = "ERROR", rate_per_min: int = 20):
        self.url = (webhook_url or "").strip()
        self.min = _LEVELS.get((min_level or "ERROR").upper(), 40)
        self.rate = rate_per_min
        self._q: queue.Queue = queue.Queue(maxsize=1000)
        self._stop = threading.Event()
        self._sent: deque = deque()
        self._thread: threading.Thread | None = None
        if self.url:
            self._thread = threading.Thread(target=self._run, name="app-log-alert", daemon=True)
            self._thread.start()

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def feed(self, records: list[dict]) -> None:
        if not self.url:
            return
        for r in records:
            if _LEVELS.get(r.get("level", "INFO"), 20) >= self.min:
                try:
                    self._q.put_nowait(r)
                except queue.Full:
                    pass

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    # -- internals --------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                rec = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            if self._rate_ok():
                self._post(rec)

    def _rate_ok(self) -> bool:
        now = time.monotonic()
        while self._sent and now - self._sent[0] > 60:
            self._sent.popleft()
        if len(self._sent) >= self.rate:
            return False
        self._sent.append(now)
        return True

    def _post(self, rec: dict) -> None:
        try:
            content = (
                f"[{rec.get('level')}] {rec.get('event')}\n"
                f"source={rec.get('source')} logger={rec.get('logger')}\n"
                f"trace_id={rec.get('trace_id')} actor={rec.get('actor')}\n"
                f"{rec.get('message') or rec.get('exc_message') or ''}"
            )
            body = json.dumps({"msgtype": "text", "text": {"content": content}}).encode("utf-8")
            req = urllib.request.Request(
                self.url, data=body, headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=5)  # noqa: S310 - operator-configured URL
        except Exception:  # noqa: BLE001 - alerting is best-effort
            pass
