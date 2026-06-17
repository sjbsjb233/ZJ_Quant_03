"""Auth routes (doc §3.2 /auth, §9.1): login / refresh / logout / me."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..db.base import get_db
from ..db.models import User
from ..deps import get_current_user
from ..errors import ApiError
from ..logging import app_logger
from .security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# 当用户名不存在时,对该常量哈希做一次等价的 PBKDF2 校验,
# 使失败路径与正常路径耗时相当,避免通过响应时延枚举用户名。
_DUMMY_HASH = hash_password(os.urandom(16).hex())


class LoginRequest(BaseModel):
    username: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


def user_to_dict(u: User) -> dict:
    return {
        "id": u.id,
        "username": u.username,
        "email": u.email,
        "nickname": u.nickname,
        "role": u.role,
        "is_active": u.is_active,
        "created_at": u.created_at,
    }


@router.post("/login")
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else None
    user = db.query(User).filter(User.username == body.username).first()
    # 用户不存在时也走一次哈希校验,保持失败路径耗时一致(防用户名枚举时序侧信道)。
    if user is None:
        verify_password(body.password, _DUMMY_HASH)
        ok = False
    else:
        ok = verify_password(body.password, user.password_hash)
    if not ok:
        app_logger.warn("auth.login.failed", username=body.username,
                        reason="bad_credentials", ip=ip)
        raise ApiError("UNAUTHENTICATED", "用户名或密码错误")
    if not user.is_active:
        app_logger.warn("auth.login.failed", username=body.username,
                        reason="inactive", ip=ip)
        raise ApiError("PERMISSION_DENIED", "该账户已停用")
    app_logger.info("auth.login.success", actor=user.username, ip=ip)
    return {
        "access_token": create_access_token(user.username, user.role),
        "refresh_token": create_refresh_token(user.username, user.role),
        "token_type": "bearer",
        "user": user_to_dict(user),
    }


@router.post("/refresh")
def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    payload = decode_token(body.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise ApiError("UNAUTHENTICATED", "刷新令牌无效或已过期")
    user = db.query(User).filter(User.username == payload.get("sub")).first()
    if user is None or not user.is_active:
        raise ApiError("UNAUTHENTICATED", "用户不存在或已停用")
    return {
        "access_token": create_access_token(user.username, user.role),
        "token_type": "bearer",
    }


@router.post("/logout")
def logout(user: User = Depends(get_current_user)):
    # Stateless JWT: client drops the token. Recorded for symmetry.
    app_logger.info("auth.logout", actor=user.username)
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return user_to_dict(user)
