"""Sensitive-field redaction (全链路日志方案 v2.0 §7.6 / §2.2 隐私安全).

Before any structured payload is persisted or broadcast, dict keys whose name
contains a sensitive token (``password`` / ``token`` / ``authorization`` /
``secret`` / ``api_key`` ...) are replaced with ``"***"``. The match is a
case-insensitive substring test so ``access_token`` / ``X-Authorization`` are
caught too.
"""
from __future__ import annotations

from typing import Any

REDACTED = "***"
_MAX_DEPTH = 6


def redact(value: Any, sensitive_keys: list[str], _depth: int = 0) -> Any:
    """Recursively replace values under sensitive keys; structure is preserved."""
    if _depth > _MAX_DEPTH:
        return value
    if isinstance(value, dict):
        out: dict = {}
        for k, v in value.items():
            if isinstance(k, str) and _is_sensitive(k, sensitive_keys):
                out[k] = REDACTED
            else:
                out[k] = redact(v, sensitive_keys, _depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(v, sensitive_keys, _depth + 1) for v in value]
    return value


def _is_sensitive(key: str, sensitive_keys: list[str]) -> bool:
    low = key.lower()
    return any(token in low for token in sensitive_keys)
