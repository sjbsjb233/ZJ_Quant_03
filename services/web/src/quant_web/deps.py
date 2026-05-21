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


def get_auditor(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Auditor:
    return Auditor(db=db, actor=user.username, ip=request.client.host if request.client else None,
                   trace_id=uuid.uuid4().hex)
