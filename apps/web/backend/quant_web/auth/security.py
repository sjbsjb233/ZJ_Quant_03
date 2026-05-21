"""Password hashing (PBKDF2, stdlib) + JWT issue/verify (python-jose)."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import os

from jose import JWTError, jwt

from ..config import get_settings

_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, dk_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iters))
        return hmac.compare_digest(dk, expected)
    except Exception:  # noqa: BLE001
        return False


def _token(sub: str, role: str, token_type: str, minutes: int) -> str:
    s = get_settings()
    now = dt.datetime.now(dt.timezone.utc)
    payload = {
        "sub": sub,
        "role": role,
        "type": token_type,
        "iat": now,
        "exp": now + dt.timedelta(minutes=minutes),
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def create_access_token(sub: str, role: str) -> str:
    return _token(sub, role, "access", get_settings().access_token_expire_minutes)


def create_refresh_token(sub: str, role: str) -> str:
    return _token(sub, role, "refresh", get_settings().refresh_token_expire_minutes)


def decode_token(token: str) -> dict | None:
    s = get_settings()
    try:
        return jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except JWTError:
        return None
