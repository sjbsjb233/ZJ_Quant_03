"""In-process SSE fan-out for the live log stream (全链路日志方案 v2.0 §7.4).

Each connected admin gets its own ``asyncio.Queue``. Producers (the request
threads, the data-layer sink, the store's background thread) are *not* on the
event loop, so :meth:`SSEHub.broadcast` hands records to each client's loop with
``call_soon_threadsafe``. Slow/full clients drop records rather than blocking a
producer. Connections are capped (doc §11: admin ≤ 10) to bound memory.
"""
from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field

_LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40, "CRITICAL": 50}
_MAX_CLIENTS = 10
_CLIENT_QUEUE_MAX = 2000


@dataclass
class _Client:
    queue: asyncio.Queue
    loop: asyncio.AbstractEventLoop
    min_level: int = 0
    sources: set[str] | None = None
    cid: int = 0
    dropped: int = field(default=0)

    def wants(self, record: dict) -> bool:
        if _LEVELS.get(record.get("level", "INFO"), 20) < self.min_level:
            return False
        if self.sources is not None and record.get("source") not in self.sources:
            return False
        return True


class SSEHub:
    def __init__(self) -> None:
        self._clients: dict[int, _Client] = {}
        self._ids = itertools.count(1)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def register(self, level: str | None = None, sources: set[str] | None = None) -> _Client | None:
        """Register the *current* coroutine's queue. Returns None if at capacity."""
        if len(self._clients) >= _MAX_CLIENTS:
            return None
        client = _Client(
            queue=asyncio.Queue(maxsize=_CLIENT_QUEUE_MAX),
            loop=asyncio.get_running_loop(),
            min_level=_LEVELS.get((level or "DEBUG").upper(), 0),
            sources=sources,
            cid=next(self._ids),
        )
        self._clients[client.cid] = client
        return client

    def unregister(self, client: _Client | None) -> None:
        if client is not None:
            self._clients.pop(client.cid, None)

    def broadcast(self, record: dict) -> None:
        """Fan a record out to every interested client (safe from any thread)."""
        for client in list(self._clients.values()):
            if not client.wants(record):
                continue
            try:
                client.loop.call_soon_threadsafe(self._offer, client, record)
            except RuntimeError:  # pragma: no cover - loop already closed
                self._clients.pop(client.cid, None)

    @staticmethod
    def _offer(client: _Client, record: dict) -> None:
        try:
            client.queue.put_nowait(record)
        except asyncio.QueueFull:
            client.dropped += 1


# module-level singleton — logging is cross-cutting and not request-scoped
hub = SSEHub()


def broadcast(record: dict) -> None:
    hub.broadcast(record)
