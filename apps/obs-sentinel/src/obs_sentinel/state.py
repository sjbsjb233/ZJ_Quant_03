"""Shared in-memory status frame + SSE pub/sub (可观测层重构方案 §5.3 / §4.4).

The sentinel is *无状态化* (§11): the frame is rebuilt from docker + probes every
scan, never persisted. ``SentinelState`` is the bridge between the scan loop
(producer) and the HTTP/SSE service (consumers): the loop calls :meth:`publish`
with a fresh frame; each SSE client gets its own bounded queue and is notified
event-driven (on every published frame) plus a periodic heartbeat from the server.
A new client is handed the latest snapshot immediately on connect.
"""
from __future__ import annotations

import queue
import threading

_CLIENT_QUEUE_MAX = 64
_MAX_CLIENTS = 16


class SentinelState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame: dict | None = None
        self._subscribers: set[queue.Queue] = set()

    # -- producer side -------------------------------------------------------
    def publish(self, frame: dict) -> None:
        with self._lock:
            self._frame = frame
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait(frame)
            except queue.Full:
                # drop the oldest, keep the newest — the frame is a full snapshot,
                # so a slow client never needs the intermediate states.
                try:
                    q.get_nowait()
                    q.put_nowait(frame)
                except (queue.Empty, queue.Full):
                    pass

    def current(self) -> dict | None:
        with self._lock:
            return self._frame

    # -- consumer side (SSE) -------------------------------------------------
    def subscribe(self) -> queue.Queue | None:
        """Register a per-client queue, primed with the current frame. Returns
        None when at capacity (bounds memory like the log SSE hub)."""
        q: queue.Queue = queue.Queue(maxsize=_CLIENT_QUEUE_MAX)
        with self._lock:
            if len(self._subscribers) >= _MAX_CLIENTS:
                return None
            self._subscribers.add(q)
            frame = self._frame
        if frame is not None:
            try:
                q.put_nowait(frame)
            except queue.Full:
                pass
        return q

    def unsubscribe(self, q: queue.Queue | None) -> None:
        if q is None:
            return
        with self._lock:
            self._subscribers.discard(q)

    @property
    def client_count(self) -> int:
        with self._lock:
            return len(self._subscribers)
