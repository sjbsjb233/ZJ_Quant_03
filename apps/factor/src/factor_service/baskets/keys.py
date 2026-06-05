"""键体系 + 窗口描述符 — 因子层重构 §5.

The geology under the whole增量/可比性 mechanism:

  common_key     = sha1(canonical(shared_config 含窗口描述符, 不含具体日期))[:16]
  item_key       = sha1(common_key | factor_id | version | canonical(params))[:16]
  comparable_key = sha1(canonical(shared_config 的可比子集))[:16]

The hard rule (§5.3): the key uses the *window descriptor* (``rolling:365d``),
never a concrete date — so the ledger's identity is stable while data rolls in.
Concrete dates live on ``basket_run.resolved_start/end`` (record fields).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from typing import Any

# Fields of shared_config that participate in the common config (§5.2). These
# are all EvalConfig fields except ``extras`` (item params), ``start/end`` (the
# window is a descriptor instead), and run-only fields (``lane``).
SHARED_FIELDS = (
    "universe_id",
    "rebalance",
    "horizons",
    "primary_horizon",
    "ic_type",
    "return_price",
    "winsorize",
    "winsorize_n",
    "standardize",
    "neutralize_size",
    "size_field",
    "neutralize_industry",
    "classification_system",
    "industry_level",
    "quantiles",
    "compute_long_short",
    "compute_turnover",
    "compute_autocorr",
    "layer_weight",
    "cost_bps",
)

# 可比子集 (§5.2) — common config minus run-only / cosmetic fields. v1.1+: this
# is an *information* field (口径指纹 display + robustness diff), not a driver.
COMPARABLE_FIELDS = (
    "universe_id",
    "rebalance",
    "primary_horizon",
    "horizons",
    "ic_type",
    "return_price",
    "winsorize",
    "winsorize_n",
    "standardize",
    "neutralize_size",
    "size_field",
    "neutralize_industry",
    "classification_system",
    "industry_level",
    "quantiles",
    "layer_weight",
    "cost_bps",
)


# ─────────────────────────────────────────────────────────────────────────────
# Window descriptor (§5.1)
class WindowError(ValueError):
    """Raised on a malformed window descriptor (→ INVALID_PARAMETER)."""


_SPAN_RE = re.compile(r"^(\d+)(d|td)$")


def parse_span(span: str) -> tuple[int, str]:
    """``"365d"`` → ``(365, "d")``; ``"252td"`` → ``(252, "td")``."""
    m = _SPAN_RE.match(str(span).strip().lower())
    if not m:
        raise WindowError(f"非法窗口跨度 '{span}' (期望如 365d / 252td)")
    return int(m.group(1)), m.group(2)


def window_descriptor(window: dict[str, Any] | None) -> str:
    """Canonical descriptor string used in the key & for display."""
    w = dict(window or {})
    mode = w.get("mode", "rolling")
    if mode == "rolling":
        n, unit = parse_span(w.get("span", "365d"))
        return f"rolling:{n}{unit}"
    if mode == "fixed":
        start = _as_iso(w.get("start"))
        end = _as_iso(w.get("end"))
        if not (start and end):
            raise WindowError("固定窗口需要 start 与 end")
        try:
            sd, ed = _parse_date(start), _parse_date(end)
        except (ValueError, TypeError):
            raise WindowError(f"固定窗口日期非法: {start} / {end}")
        if sd >= ed:
            raise WindowError(f"固定窗口 start 必须早于 end ({start} ≥ {end})")
        return f"fixed:{start}:{end}"
    raise WindowError(f"非法窗口模式 '{mode}'")


def normalize_window(window: dict[str, Any] | None) -> dict[str, Any]:
    """Pin the descriptor's structural form (drops any incidental keys)."""
    w = dict(window or {})
    mode = w.get("mode", "rolling")
    if mode == "rolling":
        n, unit = parse_span(w.get("span", "365d"))
        return {"mode": "rolling", "span": f"{n}{unit}"}
    if mode == "fixed":
        return {"mode": "fixed", "start": _as_iso(w.get("start")),
                "end": _as_iso(w.get("end"))}
    raise WindowError(f"非法窗口模式 '{mode}'")


def is_fixed(window: dict[str, Any] | None) -> bool:
    return dict(window or {}).get("mode") == "fixed"


def resolve_window(
    window: dict[str, Any] | None,
    as_of: dt.date,
    *,
    trading_days: list[dt.date] | None = None,
) -> tuple[dt.date, dt.date]:
    """Resolve a descriptor to concrete ``(start, end)`` at run-creation time.

    ``rolling:Nd``  → end=as_of, start=as_of − N natural days.
    ``rolling:Ntd``→ end=as_of, start = N-th trading day back (needs calendar).
    ``fixed``       → (start, end) verbatim (``as_of`` ignored).
    """
    w = normalize_window(window)
    if w["mode"] == "fixed":
        return _parse_date(w["start"]), _parse_date(w["end"])
    n, unit = parse_span(w["span"])
    end = as_of
    if unit == "d":
        return end - dt.timedelta(days=n), end
    # trading-day count back
    if trading_days:
        days = [d for d in sorted(trading_days) if d <= as_of]
        if len(days) >= n:
            return days[-n], end
        return days[0] if days else (end - dt.timedelta(days=n * 2)), end
    # no calendar available — approximate a trading day as ~1.45 natural days
    return end - dt.timedelta(days=int(n * 1.45)), end


# ─────────────────────────────────────────────────────────────────────────────
# Canonicalization + keys
def _as_iso(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()[:10]
    return str(v)


def _parse_date(v: Any) -> dt.date:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v)[:10])


def _canon(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _canon(obj[k]) for k in sorted(obj.keys())}
    if isinstance(obj, (list, tuple)):
        return [_canon(x) for x in obj]
    if isinstance(obj, (dt.date, dt.datetime)):
        return _as_iso(obj)
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, float) and obj.is_integer():
        return int(obj)  # 5.0 == 5, 30.0 == 30 → stable across input typing
    return obj


def canonical_json(obj: Any) -> str:
    return json.dumps(_canon(obj), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def _sha16(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:16]


def normalize_shared_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Pick exactly SHARED_FIELDS + the window descriptor, coercing types so
    incidental ordering / typing / extra keys never change a key (§14.2 L1)."""
    raw = dict(raw or {})
    out: dict[str, Any] = {}
    for f in SHARED_FIELDS:
        if f not in raw:
            continue
        v = raw[f]
        if f == "horizons" and isinstance(v, (list, tuple)):
            v = sorted({int(x) for x in v})  # order-insensitive
        out[f] = v
    out["window"] = normalize_window(raw.get("window"))
    return out


def common_key(shared_config: dict[str, Any] | None) -> str:
    return _sha16(canonical_json(normalize_shared_config(shared_config)))


def params_digest(params: dict[str, Any] | None) -> str:
    return _sha16(canonical_json(params or {}))


def item_key(
    ckey: str, factor_id: str, version: str, params: dict[str, Any] | None
) -> str:
    # JSON-array join (not "|".join) so a "|" inside factor_id/version — both
    # user-controllable for uploads — can't shift the boundary and alias two
    # distinct items onto the same ledger (review M1).
    return _sha16(canonical_json([ckey, str(factor_id), str(version), params or {}]))


def comparable_key(shared_config: dict[str, Any] | None) -> str:
    norm = normalize_shared_config(shared_config)
    sub = {f: norm[f] for f in COMPARABLE_FIELDS if f in norm}
    sub["window"] = norm.get("window")
    return _sha16(canonical_json(sub))


# ─────────────────────────────────────────────────────────────────────────────
def eval_config_dict(
    shared_config: dict[str, Any],
    resolved_start: dt.date,
    resolved_end: dt.date,
) -> dict[str, Any]:
    """Build a dict that ``EvalConfig.from_dict`` accepts: shared fields +
    concrete ``start/end`` (the descriptor is dropped — the engine wants dates).
    """
    d = {f: shared_config[f] for f in SHARED_FIELDS if f in shared_config}
    d["start"] = resolved_start.isoformat()
    d["end"] = resolved_end.isoformat()
    return d


def calibration_summary(shared_config: dict[str, Any]) -> dict[str, Any]:
    """The 口径摘要 chips/hover (§10.5) — human-readable common-config digest."""
    norm = normalize_shared_config(shared_config)
    w = norm.get("window") or {}
    if w.get("mode") == "fixed":
        win = f"固定 {w.get('start')}~{w.get('end')}"
    else:
        win = f"滚动 {w.get('span')}"
    return {
        "universe_id": norm.get("universe_id"),
        "window": win,
        "window_descriptor": window_descriptor(norm.get("window")),
        "rebalance": norm.get("rebalance"),
        "primary_horizon": norm.get("primary_horizon"),
        "ic_type": norm.get("ic_type"),
        "return_price": norm.get("return_price"),
        "neutralize_industry": norm.get("neutralize_industry"),
        "neutralize_size": norm.get("neutralize_size"),
        "classification_system": norm.get("classification_system"),
    }
