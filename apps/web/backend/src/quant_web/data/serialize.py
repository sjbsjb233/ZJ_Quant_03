"""Arrow/record -> JSON-safe helpers (doc §5.2.4 notes).

Dates become ISO strings, ``_extras`` MAP cells (arrow returns list-of-tuples)
become objects, and column metadata is exposed so the frontend can build typed
headers. Bigints beyond JS's safe range are stringified when ``strict_int``.
"""
from __future__ import annotations

import datetime as dt
import math

import pyarrow as pa

_JS_MAX_SAFE = 2**53 - 1

_TYPE_NAME = {
    pa.string(): "VARCHAR",
    pa.float64(): "DOUBLE",
    pa.int64(): "BIGINT",
    pa.int32(): "INTEGER",
    pa.bool_(): "BOOLEAN",
    pa.date32(): "DATE",
}


def duck_type(arrow_type) -> str:
    for t, name in _TYPE_NAME.items():
        if arrow_type.equals(t):
            return name
    if pa.types.is_timestamp(arrow_type):
        return "TIMESTAMP"
    if pa.types.is_map(arrow_type):
        return "MAP"
    return str(arrow_type).upper()


def jsonify(value, strict_int: bool = False):
    if value is None:
        return None
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if isinstance(value, dict):
        return {str(k): jsonify(v, strict_int) for k, v in value.items()}
    if isinstance(value, list):
        # an _extras MAP cell arrives as [(k, v), ...]
        if value and isinstance(value[0], tuple) and len(value[0]) == 2:
            return {str(k): jsonify(v, strict_int) for k, v in value}
        return [jsonify(v, strict_int) for v in value]
    if strict_int and isinstance(value, int) and abs(value) > _JS_MAX_SAFE:
        return str(value)
    # NaN/±Inf 不是合法 JSON token,默认编码器会写出裸 NaN/Infinity,
    # 导致前端 fetch().json() 整段解析失败 -> 统一降级为 null
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def row_to_json(row: dict, strict_int: bool = False) -> dict:
    return {k: jsonify(v, strict_int) for k, v in row.items()}


def table_payload(table: pa.Table, strict_int: bool = False) -> tuple[list[dict], list[dict]]:
    """Return ``(columns, items)`` for an arrow table."""
    columns = [{"name": f.name, "type": duck_type(f.type)} for f in table.schema]
    items = [row_to_json(r, strict_int) for r in table.to_pylist()]
    return columns, items
