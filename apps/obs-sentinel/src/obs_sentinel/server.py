"""Read-only HTTP/SSE status service (可观测层重构方案 §5.2 / §7.1 / §10).

The first-layer plane's *direct line to the frontend*: three GET-only endpoints,
no command/write surface, no ``docker.sock`` exposure. Served by a stdlib
``ThreadingHTTPServer`` so concurrent SSE clients each run on their own thread —
no extra dependency, keeping the watcher image tiny and robust.

  GET /obs/v1/health        — liveness of the sentinel itself (unauth, for D12/probe)
  GET /obs/v1/status        — JWT — full snapshot of the current status frame
  GET /obs/v1/status/stream — JWT (token=… query, EventSource can't set headers) —
                              SSE: snapshot frame on connect, then a frame on every
                              transition + a periodic ``: keep-alive`` heartbeat.

Auth: a JWT signed with the shared ``JWT_SECRET`` (HS256), verified **locally** —
the sentinel never calls back into web-backend (that would re-couple the planes).
When no secret is configured (dev/tests) the endpoints are open, mirroring the
backend's dev default.
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .config import Config
from .jwtmini import verify as jwt_verify
from .state import SentinelState


def _log(msg: str) -> None:
    sys.stdout.write(f"[obs-sentinel:http] {dt.datetime.utcnow().isoformat()}Z {msg}\n")
    sys.stdout.flush()


def _make_handler(cfg: Config, state: SentinelState):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "obs-sentinel/1.0"

        # silence the default noisy stderr access log
        def log_message(self, *args) -> None:  # noqa: D401
            return

        # -- auth ------------------------------------------------------------
        def _authed(self, params: dict) -> bool:
            if not cfg.jwt_secret:
                return True                        # dev/tests: no secret → open
            token = None
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                token = auth[7:].strip()
            if not token:
                token = (params.get("token") or [None])[0]
            return jwt_verify(token, cfg.jwt_secret, algorithm=cfg.jwt_algorithm) is not None

        # -- responses -------------------------------------------------------
        def _json(self, status: int, obj: dict) -> None:
            body = json.dumps(obj, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            path = parsed.path.rstrip("/") or "/"
            params = parse_qs(parsed.query)
            try:
                if path == "/obs/v1/health":
                    return self._json(200, {"status": "ok", "source": "sentinel",
                                            "sse_clients": state.client_count})
                if path == "/obs/v1/status":
                    if not self._authed(params):
                        return self._json(401, {"error": "unauthorized"})
                    frame = state.current()
                    if frame is None:
                        return self._json(503, {"error": "no_frame_yet", "source": "sentinel"})
                    return self._json(200, frame)
                if path == "/obs/v1/status/stream":
                    if not self._authed(params):
                        return self._json(401, {"error": "unauthorized"})
                    return self._stream()
                return self._json(404, {"error": "not_found"})
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
            except Exception as exc:  # noqa: BLE001 - never let one request kill the thread
                _log(f"handler error {path}: {exc}")
                try:
                    self._json(500, {"error": "internal"})
                except Exception:  # noqa: BLE001
                    self.close_connection = True

        # -- SSE -------------------------------------------------------------
        def _stream(self) -> None:
            q = state.subscribe()
            if q is None:
                return self._json(503, {"error": "too_many_clients"})
            try:
                # raw SSE headers — stream an unbounded body, no Content-Length.
                self.wfile.write(b"HTTP/1.1 200 OK\r\n")
                for h in (b"Content-Type: text/event-stream",
                          b"Cache-Control: no-cache",
                          b"Connection: keep-alive",
                          b"X-Accel-Buffering: no"):
                    self.wfile.write(h + b"\r\n")
                self.wfile.write(b"\r\n")
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
                while True:
                    try:
                        frame = q.get(timeout=max(2, cfg.status_push_sec))
                        chunk = f"event: status\ndata: {json.dumps(frame, default=str)}\n\n"
                    except queue.Empty:
                        chunk = ": keep-alive\n\n"     # heartbeat 防中间层断连
                    self.wfile.write(chunk.encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass                                   # client disconnected → release
            finally:
                state.unsubscribe(q)
                self.close_connection = True

    return Handler


def serve(cfg: Config, state: SentinelState) -> ThreadingHTTPServer:
    """Start the status service on a daemon thread; returns the server handle."""
    handler = _make_handler(cfg, state)
    httpd = ThreadingHTTPServer(("0.0.0.0", cfg.http_port), handler)
    httpd.daemon_threads = True
    t = threading.Thread(target=httpd.serve_forever, name="obs-http", daemon=True)
    t.start()
    _log(f"status service listening on :{cfg.http_port} "
         f"(auth={'jwt' if cfg.jwt_secret else 'open(dev)'})")
    return httpd
