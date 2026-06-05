"""篮内相关性 — Pearson/Spearman on windowed settled IC series (§7.5 / §8).

Input is the *increment-maintained ledger*: each item's settled IC series. The
matrix is always recomputed full over the window's common dates (cheap — N items
× ~50 weekly points), so it is both correct and нет"增量矩阵更新" complexity.
"""
from __future__ import annotations

from typing import Any

import numpy as np


def _rankdata(a: np.ndarray) -> np.ndarray:
    """Average-rank (ties shared) — for Spearman."""
    order = a.argsort()
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, len(a) + 1)
    # average ties
    _, inv, counts = np.unique(a, return_inverse=True, return_counts=True)
    sums = np.zeros(len(counts))
    np.add.at(sums, inv, ranks)
    avg = sums / counts
    return avg[inv]


def _corr(x: np.ndarray, y: np.ndarray, method: str) -> float | None:
    if len(x) < 2:
        return None
    if method == "spearman":
        x, y = _rankdata(x), _rankdata(y)
    if np.std(x) == 0 or np.std(y) == 0:
        return None
    c = float(np.corrcoef(x, y)[0, 1])
    if not np.isfinite(c):
        return None
    return c


def compute_ic_correlation(
    series_by_item: dict[str, dict[str, float]],
    labels: dict[str, str],
    *,
    method: str = "pearson",
    threshold: float = 0.7,
    min_periods: int = 12,
) -> dict[str, Any]:
    """Pairwise-complete correlation over settled IC series.

    Returns labels (item order), the matrix (item_key→item_key→ρ), high pairs
    (|ρ|≥threshold), simple clusters (connected components at threshold), and
    each pair's aligned sample count.
    """
    items = list(series_by_item.keys())
    n = len(items)
    matrix: dict[str, dict[str, float | None]] = {a: {} for a in items}
    aligned: dict[str, dict[str, int]] = {a: {} for a in items}
    high_pairs: list[dict] = []

    for i in range(n):
        a = items[i]
        matrix[a][a] = 1.0
        aligned[a][a] = len(series_by_item[a])
        for j in range(i + 1, n):
            b = items[j]
            da, db = series_by_item[a], series_by_item[b]
            common = sorted(set(da) & set(db))
            nshared = len(common)
            aligned[a][b] = aligned[b][a] = nshared
            if nshared < max(2, min_periods):
                matrix[a][b] = matrix[b][a] = None
                continue
            xa = np.array([da[d] for d in common], dtype=float)
            xb = np.array([db[d] for d in common], dtype=float)
            rho = _corr(xa, xb, method)
            matrix[a][b] = matrix[b][a] = rho
            if rho is not None and abs(rho) >= threshold:
                high_pairs.append({
                    "a": a, "b": b,
                    "a_label": labels.get(a, a), "b_label": labels.get(b, b),
                    "rho": round(rho, 4), "n": nshared,
                })

    high_pairs.sort(key=lambda p: -abs(p["rho"]))

    # clusters — connected components on |ρ|≥threshold edges (§8.4 approximation)
    parent = {a: a for a in items}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    for p in high_pairs:
        union(p["a"], p["b"])
    comp: dict[str, list[str]] = {}
    for a in items:
        comp.setdefault(find(a), []).append(a)
    clusters = [sorted(members) for members in comp.values()]
    clusters.sort(key=lambda c: -len(c))

    return {
        "labels": [{"item_key": k, "label": labels.get(k, k)} for k in items],
        "order": items,
        "matrix": matrix,
        "aligned_n": aligned,
        "high_pairs": high_pairs,
        "clusters": clusters,
        "method": method,
        "threshold": threshold,
    }


def flat_matrix(corr_json: dict) -> dict[str, dict[str, float]]:
    """Coerce a stored corr_json matrix into {item_key:{item_key:|ρ|}} for prune."""
    raw = corr_json.get("matrix") or {}
    out: dict[str, dict[str, float]] = {}
    for a, row in raw.items():
        out[a] = {}
        for b, v in (row or {}).items():
            out[a][b] = abs(float(v)) if v is not None else 0.0
    return out
