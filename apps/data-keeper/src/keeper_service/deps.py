"""Dependency injection for data-keeper (auth + reader + db session).

Auth mirrors factor_service: the calling identity is resolved from the trace-id
middleware's ContextVars, which were filled from the web proxy's trusted
``X-Forwarded-User/-Role`` headers (or a direct JWT). data-keeper does not run
RBAC of its own beyond a role floor — web already authenticated the user; these
checks are defence-in-depth for a keeper hit directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Generator

from fastapi import Depends
from quant_data import DataReader
from sqlalchemy.orm import Session

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


def get_root() -> str:
    return get_settings().quant_data_root


def get_db() -> Generator[Session, None, None]:
    db = _db_base.SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_reader() -> DataReader:
    """A governed reader bound to the (writable) data root — used by command
    endpoints for validation reads (e.g. count_batch before invalidate)."""
    return DataReader(root=get_settings().quant_data_root)


def current_user() -> CallingUser:
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
