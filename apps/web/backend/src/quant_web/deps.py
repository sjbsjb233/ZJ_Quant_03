"""FastAPI dependencies: settings, data root, DataReader, current user, RBAC, auditor."""
from __future__ import annotations

import uuid

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from quant_data import DataReader

from .audit.log import Auditor
from .auth.security import decode_token
from .config import Settings, get_settings
from .db.base import get_db
from .db.models import User
from .errors import ApiError
from .logging.context import trace_id_var

bearer = HTTPBearer(auto_error=False)

# role hierarchy (doc §9.2): admin ⊃ editor ⊃ viewer
ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2}


def settings_dep() -> Settings:
    return get_settings()


def get_root() -> str:
    return get_settings().quant_data_root


def get_reader() -> DataReader:
    """A fresh governed reader bound to the configured data root."""
    return DataReader(root=get_settings().quant_data_root)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None or not creds.credentials:
        raise ApiError("UNAUTHENTICATED", "未登录或缺少访问令牌")
    payload = decode_token(creds.credentials)
    if not payload or payload.get("type") != "access":
        raise ApiError("UNAUTHENTICATED", "访问令牌无效或已过期")
    user = db.query(User).filter(User.username == payload.get("sub")).first()
    if user is None or not user.is_active:
        raise ApiError("UNAUTHENTICATED", "用户不存在或已停用")
    return user


def get_current_user_sse(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    """Like :func:`get_current_user` but also accepts the access token via a
    ``?token=`` query param. EventSource (SSE) cannot set an Authorization
    header, so the run-log stream proxy must fall back to the query param the
    frontend's ``runLogStreamUrl`` already supplies (mirrors /logs/stream)."""
    raw = creds.credentials if (creds and creds.credentials) else request.query_params.get("token")
    if not raw:
        raise ApiError("UNAUTHENTICATED", "未登录或缺少访问令牌")
    payload = decode_token(raw)
    if not payload or payload.get("type") != "access":
        raise ApiError("UNAUTHENTICATED", "访问令牌无效或已过期")
    user = db.query(User).filter(User.username == payload.get("sub")).first()
    if user is None or not user.is_active:
        raise ApiError("UNAUTHENTICATED", "用户不存在或已停用")
    return user


def require_role(min_role: str):
    """Dependency factory enforcing a minimum role (doc §9.2)."""
    threshold = ROLE_RANK[min_role]

    def _dep(user: User = Depends(get_current_user)) -> User:
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


# --- layered log access (容器化重构 V3 §4.2) -----------------------------------
# The four log layers/sources. admin sees all of them; other roles are narrowed
# by Settings.log_visible_sources. The narrowing is the security boundary — the
# frontend's lockSource is only UX (V3 §6.2).
ALL_SOURCES = {"data", "factor", "backend", "frontend"}


def allowed_sources_for(user: User) -> set[str]:
    """The set of log ``source`` layers this user is allowed to see."""
    if user.role == "admin":
        return set(ALL_SOURCES)
    return get_settings().log_sources_by_role.get(user.role, set())


def require_log_access(user: User = Depends(get_current_user)) -> User:
    """Gate the log-centre endpoints: any role with ≥1 visible source may enter.

    Replaces the old admin-only gate so the data team can see ``data`` and the
    factor team ``factor`` (V3 §4.2). The per-source narrowing inside the routes
    (intersecting the requested ``source`` with :func:`allowed_sources_for`) is
    what actually prevents a viewer from pulling layers they shouldn't.
    """
    if not allowed_sources_for(user):
        raise ApiError("PERMISSION_DENIED", "当前角色无任何日志查看权限")
    return user


def get_auditor(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Auditor:
    # Reuse the request's trace_id (set by TraceIdMiddleware) so the audit row,
    # the technical app_log line, and the data-layer logs all share one id.
    trace_id = trace_id_var.get() or ("t_" + uuid.uuid4().hex[:16])
    return Auditor(db=db, actor=user.username, ip=request.client.host if request.client else None,
                   trace_id=trace_id)
