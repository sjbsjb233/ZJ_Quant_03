"""Backend logging platform (全链路日志方案 v2.0 §5).

Public surface mirrors the data layer's ``quant_data.logging``:

    from .logging import app_logger, get_logger
    app_logger.info("auth.login.success", actor=user.username)
"""
from __future__ import annotations

from . import context
from .logger import app_logger, configure, get_logger
from .sinks import install_sinks
from .sse import broadcast, hub
from .store import AppLogStore, get_store, init_store, set_store

__all__ = [
    "app_logger",
    "configure",
    "get_logger",
    "install_sinks",
    "broadcast",
    "hub",
    "AppLogStore",
    "init_store",
    "get_store",
    "set_store",
    "context",
]
