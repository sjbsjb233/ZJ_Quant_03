"""Minimal local HS256 JWT verification (stdlib only, 可观测层重构方案 §5.2 / §10).

The status SSE/snapshot endpoints require a JWT, but the sentinel must *not* call
back into web-backend to validate it (that would re-couple the heartbeat plane to
the rich-data plane). Instead it verifies the signature locally with the shared
``JWT_SECRET`` (HS256), exactly the secret web-backend signs with. We implement
just enough of RFC 7519 — base64url, HMAC-SHA256, ``exp`` — with no PyJWT dep so
the image stays tiny and the watcher stays robust.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time


def _b64url_decode(seg: str) -> bytes:
    pad = "=" * (-len(seg) % 4)
    return base64.urlsafe_b64decode(seg + pad)


def verify(token: str, secret: str, *, algorithm: str = "HS256",
           leeway: int = 30) -> dict | None:
    """Return the decoded claims if ``token`` is a valid, unexpired HS256 JWT
    signed with ``secret``; else None. Constant-time signature compare."""
    if not token or not secret:
        return None
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
    except ValueError:
        return None
    try:
        header = json.loads(_b64url_decode(header_b64))
    except Exception:  # noqa: BLE001
        return None
    if (header.get("alg") or "").upper() != algorithm.upper() or algorithm.upper() != "HS256":
        return None
    expected = hmac.new(secret.encode("utf-8"),
                        f"{header_b64}.{payload_b64}".encode("ascii"),
                        hashlib.sha256).digest()
    try:
        given = _b64url_decode(sig_b64)
    except Exception:  # noqa: BLE001
        return None
    if not hmac.compare_digest(expected, given):
        return None
    try:
        claims = json.loads(_b64url_decode(payload_b64))
    except Exception:  # noqa: BLE001
        return None
    exp = claims.get("exp")
    if isinstance(exp, (int, float)) and time.time() > exp + leeway:
        return None
    return claims
