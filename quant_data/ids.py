"""ID + fingerprint helpers (batch ids, parameter hashes, input hashes)."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from typing import Any


def new_batch_id() -> str:
    """A fresh UUID v4, used as a batch_id (doc §2.1)."""
    return str(uuid.uuid4())


def utc_now() -> dt.datetime:
    """Naive UTC timestamp (matches the TIMESTAMP columns)."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, compact separators.

    Used both to store JSON columns and to feed the hash, so a given set of
    parameters always yields the same string and the same digest.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parameters_hash(parameters: Any) -> str:
    """SHA256 over the canonical JSON of a batch's call parameters (doc §2.1)."""
    return sha256_hex(canonical_json(parameters))
