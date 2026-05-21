"""Cross-source comparison + provenance audit (doc §7.2 / §7.4).

* diff_sources -- compare two sources' rows for the same keys before switching
  the primary source (doc §7.4 stage B).
* audit helpers -- inspect invalidated batches and supersession chains (doc §7.2).

Switching the primary source itself is just a priority change
(``metadata.sources.update_source_priority``); business code never changes.
"""
from __future__ import annotations

from .metadata import batches
from .reader import DataReader, _date_lit, _str_lit, _ts_lit
from .schemas import get_spec

# time column used for range filtering, per table
_TIME_COL = {
    "raw.bars_daily": ("date", _date_lit),
    "raw.bars_minute": ("datetime", _ts_lit),
    "raw.fundamentals": ("report_period", _date_lit),
    "raw.universe": ("date", _date_lit),
    "raw.corporate_actions": ("action_date", _date_lit),
}


def diff_sources(
    table: str,
    source_a: str,
    source_b: str,
    start=None,
    end=None,
    columns: list[str] | None = None,
    sample: int = 20,
    root=None,
) -> dict:
    """Compare the current-view rows of two sources for identical keys.

    Both sources must be active (the current view only considers active sources).
    Returns counts of keys only in A / only in B / value mismatches, plus a
    bounded sample of mismatching rows.
    """
    spec = get_spec(table)
    reader = DataReader(root=root)

    time_filters = []
    if table in _TIME_COL and (start is not None or end is not None):
        col, lit = _TIME_COL[table]
        if start is not None:
            time_filters.append(f"t.{col} >= {lit(start)}")
        if end is not None:
            time_filters.append(f"t.{col} <= {lit(end)}")

    def _rows(src):
        cv = reader.current_view(table, filters=[f"t._source = {_str_lit(src)}", *time_filters])
        return {tuple(r[k] for k in spec.primary_key): r for r in cv.to_pylist()}

    a, b = _rows(source_a), _rows(source_b)
    cols = columns or [c for c in spec.business_names() if c not in spec.primary_key]

    only_a = sorted(set(a) - set(b), key=str)
    only_b = sorted(set(b) - set(a), key=str)
    mismatches = []
    for key in set(a) & set(b):
        diffs = {c: (a[key].get(c), b[key].get(c)) for c in cols if a[key].get(c) != b[key].get(c)}
        if diffs:
            mismatches.append({"key": key, "diffs": diffs})

    return {
        "table": table,
        "source_a": source_a,
        "source_b": source_b,
        "n_a": len(a),
        "n_b": len(b),
        "only_in_a": only_a,
        "only_in_b": only_b,
        "n_only_in_a": len(only_a),
        "n_only_in_b": len(only_b),
        "n_mismatch": len(mismatches),
        "mismatches": mismatches[:sample],
    }


# --- provenance audit (doc §7.2) --------------------------------------------
def audit_invalidated(root=None) -> list[dict]:
    """Invalidated batches, most-recently-invalidated first."""
    rows = [b for b in batches.list_batches(root=root) if b["status"] == "invalidated"]
    rows.sort(key=lambda r: r.get("invalidated_at") or 0, reverse=True)
    return rows


def lineage(batch_id: str, root=None) -> list[dict]:
    """Follow the supersession chain forward from a batch (old -> newer ...)."""
    chain, seen, cur = [], set(), batch_id
    while cur and cur not in seen:
        seen.add(cur)
        b = batches.get_batch(cur, root=root)
        if b is None:
            break
        chain.append(b)
        cur = b.get("superseded_by")
    return chain
