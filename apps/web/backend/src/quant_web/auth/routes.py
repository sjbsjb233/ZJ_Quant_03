"""Auth routes (doc §3.2 /auth, §9.1): login / refresh / logout / me."""
from __future__ import annotations

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
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


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
    if user is None or not verify_password(body.password, user.password_hash):
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
