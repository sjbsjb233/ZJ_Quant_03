"""Dependency injection helpers (auth + reader + db session).

Auth: the service trusts X-Forwarded-User/-Role from the web proxy by default
(set via TrustProxy headers). If a request comes in with a direct Bearer
token, JWT is decoded inline (same secret/algorithm as web).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Generator

from fastapi import Depends
from sqlalchemy.orm import Session

from quant_data import DataReader

from . import db as _db_pkg
from .config import Settings, get_settings
from .db import base as _db_base
from .errors import ApiError
from .logging_ctx import actor_var, role_var


ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2}


@dataclass
class CallingUser:
    username: str
    role: str


def settings_dep() -> Settings:
    return get_settings()


def get_db() -> Generator[Session, None, None]:
    # Resolve SessionLocal at call time so tests can rebind() the engine.
    db = _db_base.SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_reader() -> DataReader:
    """One DataReader per request — cheap (DuckDB connect)."""
    return DataReader(root=get_settings().quant_data_root)


def current_user() -> CallingUser:
    """Resolve calling user from the trace-id middleware's ContextVar.

    The middleware fills it from X-Forwarded-User / -Role (web proxy) or by
    decoding the Authorization Bearer token directly.
    """
    actor = actor_var.get()
    role = role_var.get() or "viewer"
    if not actor:
        raise ApiError("UNAUTHENTICATED", "未登录或缺少访问令牌")
    return CallingUser(username=actor, role=role)


def require_role(min_role: str):
    threshold = ROLE_RANK[min_role]

    def _dep(user: CallingUser = Depends(current_user)) -> CallingUser:
        if ROLE_RANK.get(user.role, 0) < threshold:
            raise ApiError(
                "PERMISSION_DENIED",
                f"需要 {min_role} 及以上角色,当前角色 {user.role}",
                {"required": min_role, "actual": user.role},
            )
        return user

    return _dep


require_viewer = require_role("viewer")
require_editor = require_role("editor")
require_admin = require_role("admin")
