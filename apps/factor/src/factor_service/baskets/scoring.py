"""因子优选算法层 — 过滤 DSL · robust z 评分 · 低相关贪心剪枝 (§11.3–§11.6).

Pure numpy/python. The same code path is the backend权威复核 of the screening
工作台 (§11.6 交互契约): the frontend previews保留/剔除 in memory, the server
recomputes here at save time and is authoritative.
"""
from __future__ import annotations

from typing import Any

import numpy as np

# ─── 6 core score metrics (§11.4) — (key, direction, use_abs) ─────────────────
# direction +1: higher better · −1: lower better (negate z). use_abs: |x| first.
SCORE_METRICS: list[tuple[str, int, bool]] = [
    ("icir_annual", +1, False),
    ("rank_ic_mean", +1, True),
    ("ic_win_rate", +1, False),
    ("ls_sharpe", +1, False),
    ("ls_max_drawdown", -1, True),
    ("turnover", -1, False),
]
SCORE_KEYS = [m[0] for m in SCORE_METRICS]

# Weight templates (§11.4, v1.3 定稿). Builtin, immutable.
WEIGHT_TEMPLATES: dict[str, dict[str, float]] = {
    "ic_quality": {"icir_annual": 0.30, "rank_ic_mean": 0.25, "ic_win_rate": 0.15,
                   "ls_sharpe": 0.15, "ls_max_drawdown": 0.10, "turnover": 0.05},
    "ls_return": {"icir_annual": 0.15, "rank_ic_mean": 0.10, "ic_win_rate": 0.10,
                  "ls_sharpe": 0.35, "ls_max_drawdown": 0.20, "turnover": 0.10},
    "low_turnover": {"icir_annual": 0.20, "rank_ic_mean": 0.15, "ic_win_rate": 0.10,
                     "ls_sharpe": 0.15, "ls_max_drawdown": 0.10, "turnover": 0.30},
}
DEFAULT_TEMPLATE = "ic_quality"

# Metric white-list for filter DSL + sort (§11.3) — item_summaries numeric cols.
METRIC_WHITELIST = {
    "ic_mean", "rank_ic_mean", "icir", "icir_annual", "ic_t_stat", "ic_win_rate",
    "ls_annual_return", "ls_sharpe", "ls_max_drawdown", "long_annual_return",
    "short_annual_return", "turnover", "turnover_after_cost_ann", "coverage",
    "settled_n",
}


# ─────────────────────────────────────────────────────────────────────────────
def _x(metrics: dict, key: str, use_abs: bool) -> float | None:
    v = metrics.get(key)
    if v is None:
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if use_abs:
        v = abs(v)
    return v


def robust_z(values: list[float | None]) -> list[float]:
    """z = clip((x − median)/(1.4826·MAD), −3, +3). MAD=0 → 0. None → 0 (sinks)."""
    arr = np.array([v if v is not None else np.nan for v in values], dtype=float)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return [0.0] * len(values)
    med = float(np.median(finite))
    mad = float(np.median(np.abs(finite - med)))
    if mad == 0:
        return [0.0] * len(values)
    scale = 1.4826 * mad
    out = []
    for v in arr:
        if not np.isfinite(v):
            out.append(0.0)
        else:
            out.append(float(np.clip((v - med) / scale, -3.0, 3.0)))
    return out


def score_rows(rows: list[dict], weights: dict[str, float]) -> None:
    """Annotate each row in-place with ``score`` + ``score_detail``.

    Standardization is over the *whole cohort* passed in (§11.4 顺序约定): callers
    pass all non-hard-excluded rows so the z distribution — and thus the score —
    never drifts when display filters are applied.
    """
    if not rows:
        return
    w = {k: float(weights.get(k, 0.0)) for k in SCORE_KEYS}
    wsum = sum(abs(v) for v in w.values()) or 1.0
    # raw x per metric (with abs transform) across the cohort
    z_by_metric: dict[str, list[float]] = {}
    x_by_metric: dict[str, list[float | None]] = {}
    for key, _dir, use_abs in SCORE_METRICS:
        xs = [_x(r.get("metrics") or {}, key, use_abs) for r in rows]
        x_by_metric[key] = xs
        z_by_metric[key] = robust_z(xs)
    for i, r in enumerate(rows):
        detail = {}
        total = 0.0
        for key, direction, _use_abs in SCORE_METRICS:
            z = z_by_metric[key][i] * direction
            wt = w[key]
            contrib = z * wt
            total += contrib
            # Full precision so Σcontrib == score holds exactly (§3.7 SCO-07).
            # The frontend formats these for display (fmtNum), so no rounding here.
            detail[key] = {"x": x_by_metric[key][i], "z": z, "w": wt, "contrib": contrib}
        r["score"] = total / wsum
        r["score_detail"] = detail


# ─────────────────────────────────────────────────────────────────────────────
def _cmp(op: str, x: float, v: Any) -> bool:
    if op == "gte":
        return x >= float(v)
    if op == "lte":
        return x <= float(v)
    if op == "eq":
        return x == float(v)
    if op == "between":
        lo, hi = float(v[0]), float(v[1])
        return lo <= x <= hi
    raise ValueError(f"非法运算符 '{op}'")


def apply_filters(rows: list[dict], filters) -> list[dict]:
    """Filter rows by the DSL (§11.3). Hides rows; never rescore. Raises
    ``ValueError`` on an illegal metric key / operator (→ INVALID_PARAMETER).

    Tolerates ``filters`` arriving as a JSON string (the grid passes it via a
    query param; create_selection/prune pass it in a JSON body) — coerce here so
    every caller is robust instead of 500-ing on ``str.get`` (review/live-stack).
    """
    if isinstance(filters, str):
        import json as _json
        try:
            filters = _json.loads(filters)
        except (ValueError, TypeError):
            raise ValueError("filters 非法 JSON")
    if not filters:
        return list(rows)
    if not isinstance(filters, dict):
        raise ValueError("filters 必须是对象")
    _OPS = ("gte", "lte", "between", "eq")
    metric_rules = filters.get("metric") or []
    param_rules = filters.get("params") or []
    # Bound the DSL size (defense in depth — the GET query length also caps it):
    if len(metric_rules) + len(param_rules) > 100:
        raise ValueError("过滤条件数量超过上限(100)")
    # Validate keys AND operators UPFRONT (not lazily in _cmp) so an illegal op
    # like ">=" is a clean 400 even when no row carries that metric.
    for rule in metric_rules:
        if rule.get("k") not in METRIC_WHITELIST:
            raise ValueError(f"非法指标键 '{rule.get('k')}'")
        if rule.get("op") not in _OPS:
            raise ValueError(f"非法运算符 '{rule.get('op')}' (允许: gte/lte/between/eq)")
        if "v" not in rule:  # 比较值缺失 → 取 rule["v"] 会 KeyError → 500 (统一为 400)
            raise ValueError("过滤条件缺少比较值 v")
    for rule in param_rules:
        if "k" not in rule:  # 参数规则缺键名 → 取 rule["k"] 会 KeyError → 500
            raise ValueError("参数过滤条件缺少参数键 k")
        if rule.get("op") not in _OPS:
            raise ValueError(f"非法运算符 '{rule.get('op')}' (允许: gte/lte/between/eq)")
        if "v" not in rule:
            raise ValueError("参数过滤条件缺少比较值 v")
    flags = filters.get("flags") or {}
    fac = filters.get("factor") or {}
    param_rules = filters.get("params") or []
    q = (fac.get("q") or "").strip().lower()
    cats = set(fac.get("category") or [])
    tags_any = set(fac.get("tags_any") or [])

    out = []
    for r in rows:
        m = r.get("metrics") or {}
        ok = True
        for rule in metric_rules:
            x = m.get(rule["k"])
            if x is None:  # metric_missing never数值-killed (§11.2)
                if r.get("metric_missing"):
                    continue
                ok = False
                break
            try:
                if not _cmp(rule["op"], float(x), rule["v"]):
                    ok = False
                    break
            except (TypeError, ValueError):
                ok = False
                break
        if ok and flags.get("monotonic") and not r.get("monotonic"):
            ok = False
        if ok and cats and r.get("category") not in cats:
            ok = False
        if ok and tags_any and not (tags_any & set(r.get("tags") or [])):
            ok = False
        if ok and q:
            hay = f"{r.get('factor_id','')} {r.get('label','')} {r.get('factor_name','')}".lower()
            if q not in hay:
                ok = False
        if ok and param_rules:
            params = r.get("params") or {}
            for rule in param_rules:
                pv = params.get(rule["k"])
                try:
                    if pv is None or not _cmp(rule["op"], float(pv), rule["v"]):
                        ok = False
                        break
                except (TypeError, ValueError):
                    ok = False
                    break
        if ok:
            out.append(r)
    return out


# ─────────────────────────────────────────────────────────────────────────────
def collapse_rows(rows: list[dict], mode: str) -> list[dict]:
    """collapse=best (one row per factor_id, max score) | all (§11.5).
    Adds ``variant_count`` to surviving rows. Tie-break: item_key字典序."""
    if mode != "best":
        return sorted(rows, key=lambda r: (-(r.get("score") or 0.0), r.get("item_key", "")))
    by_factor: dict[str, list[dict]] = {}
    for r in rows:
        by_factor.setdefault(r.get("factor_id"), []).append(r)
    out = []
    for _fid, group in by_factor.items():
        group.sort(key=lambda r: (-(r.get("score") or 0.0), r.get("item_key", "")))
        best = dict(group[0])
        best["variant_count"] = len(group)
        out.append(best)
    out.sort(key=lambda r: (-(r.get("score") or 0.0), r.get("item_key", "")))
    return out


# ─────────────────────────────────────────────────────────────────────────────
def order_candidates(rows: list[dict], order_mode: str, manual_order: list[str] | None) -> list[dict]:
    """Sort candidates by剪枝优先序 (§11.6). manual → by manual_order list."""
    if order_mode == "manual":
        order = list(manual_order or [])
        cand_keys = [r["item_key"] for r in rows]
        if len(order) != len(set(order)):
            raise ValueError("manual_order 含重复候选")
        cand_set, order_set = set(cand_keys), set(order)
        missing = cand_set - order_set
        if missing:
            raise ValueError(f"manual_order 缺少候选: {sorted(missing)}")
        extra = order_set - cand_set
        if extra:
            raise ValueError(f"manual_order 含非候选项: {sorted(extra)}")
        pos = {k: i for i, k in enumerate(order)}
        return sorted(rows, key=lambda r: pos[r["item_key"]])
    if order_mode.startswith("metric:"):
        key = order_mode.split(":", 1)[1]
        desc = True
        if key.endswith("_asc"):
            key, desc = key[:-4], False
        elif key.endswith("_desc"):
            key = key[:-5]
        if key not in METRIC_WHITELIST:
            raise ValueError(f"非法排序指标 '{key}'")
        return sorted(rows, key=lambda r: ((r.get("metrics") or {}).get(key) or 0.0),
                      reverse=desc)
    # default: score desc
    return sorted(rows, key=lambda r: (-(r.get("score") or 0.0), r.get("item_key", "")))


def greedy_prune(
    candidates: list[dict],
    matrix: dict[str, dict[str, float]],
    *,
    threshold: float,
    max_n: int | None,
    forced: set[str],
    order_mode: str = "score",
    manual_order: list[str] | None = None,
) -> dict[str, Any]:
    """Deterministic O(n²) low-correlation prune (§11.6, v1.2 order).

    1) forced (manual-keep) enter first, exempt from θ and max_n.
    2) remaining in priority order; a相关 conflict is judged *before* the cap —
       a row hitting both is recorded as ``pruned_corr`` (corr evidence比超限信息量大).
    """
    if max_n is not None and max_n <= 0:
        raise ValueError("max_n 必须为正整数")
    ordered = order_candidates(candidates, order_mode, manual_order)
    forced_rows = [r for r in ordered if r["item_key"] in forced]
    rest = [r for r in ordered if r["item_key"] not in forced]

    selected: list[dict] = []
    pruned: list[dict] = []

    def _rho(a: str, b: str) -> float:
        return abs((matrix.get(a) or {}).get(b, 0.0))

    # forced first
    for r in forced_rows:
        selected.append({**r, "decision": "kept_manual", "blocked_by": None, "rho": None})

    for r in rest:
        k = r["item_key"]
        # corr conflict vs already-selected
        conflict = None
        best_rho = 0.0
        for s in selected:
            rho = _rho(k, s["item_key"])
            if rho >= threshold and rho > best_rho:
                conflict = s["item_key"]
                best_rho = rho
        if conflict is not None:
            pruned.append({**r, "decision": "pruned_corr",
                           "blocked_by": conflict, "rho": round(best_rho, 4)})
        elif max_n is not None and len([s for s in selected if s["decision"] != "kept_manual"]) >= max_n:
            pruned.append({**r, "decision": "pruned_capped", "blocked_by": None, "rho": None})
        else:
            selected.append({**r, "decision": "kept", "blocked_by": None, "rho": None})

    # rank assignment in selection order
    for i, s in enumerate(selected):
        s["rank"] = i + 1
    return {"kept": selected, "pruned": pruned}
