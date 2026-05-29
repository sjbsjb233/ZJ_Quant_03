"""factor_service — FastAPI app + SQLite-backed task queue + dispatcher.

Roles (selected via FACTOR_ROLE env var):
  - "api"     : uvicorn main:app — receives REST requests, enqueues tasks
  - "worker"  : python -m factor_service.queue.dispatcher — drains the queue

Both processes share the same SQLite file (factor.db) via a mounted volume.
"""
__version__ = "0.1.0"
