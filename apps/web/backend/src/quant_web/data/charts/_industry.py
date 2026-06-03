"""Shared industry-rollup helpers for the viz chart routes (VIZ Layer v2 §5.2/§7.2).

Single source of truth for *which industry a symbol belongs to*. The rollup
algorithm mirrors :func:`quant_factor.neutralize.industry_at_level` exactly (walk
``parent_code`` up the ``reference.sectors`` tree until the requested level is
reached) so the 行业 shown on a chart is the same 行业 used for factor
neutralisation. Keeping both口径 on one algorithm is the whole point — see §5.2.

Reference dictionaries (``reference.symbols`` / ``reference.sectors``) are small,
bounded tables (a few thousand rows); reading them whole is the existing pattern
(symbol search already does it) and never hits the bars-scale OOM path. The big,
versioned tables (bars/universe) stay aggregated inside DuckDB in the routers.
"""
from __future__ import annotations

from collections import Counter

# Same guard as neutralize._MAX_ROLLUP_DEPTH: a malformed cycle in parent_code
# can never spin forever.
_MAX_ROLLUP_DEPTH = 12


class SectorTree:
    """An in-memory view of one classification system's ``reference.sectors``."""

    def __init__(self, rows: list[dict]):
        self.by_code: dict[str, dict] = {}
        for r in rows:
            code = r.get("code")
            if code is None:
                continue
            lvl = r.get("level")
            self.by_code[code] = {
                "code": code,
                "name": r.get("name"),
                "level": int(lvl) if lvl is not None else None,
                "parent": r.get("parent_code"),
            }
        # parent_code -> [child codes]; None key holds the roots.
        self.children: dict[str | None, list[str]] = {}
        for code, node in self.by_code.items():
            self.children.setdefault(node["parent"], []).append(code)

    # -- rollup (mirrors neutralize.industry_at_level._roll) ------------------
    def roll(self, code: str | None, level: int) -> str | None:
        """Roll ``code`` up to ``level`` (1/2/3); None if impossible.

        Returns None when the leaf is *coarser* than requested (can't fabricate
        detail) or when the code / a parent is missing — never guesses.
        """
        if code is None:
            return None
        cur = code
        for _ in range(_MAX_ROLLUP_DEPTH):
            info = self.by_code.get(cur)
            if info is None:
                return None
            lv = info["level"]
            if lv == level:
                return cur
            if lv is not None and lv < level:
                return None
            parent = info["parent"]
            if parent is None:
                return None
            cur = parent
        return None

    def path(self, code: str | None) -> list[dict]:
        """Full root→leaf path for a code: [{level, code, name}, ...]."""
        chain: list[dict] = []
        cur = code
        for _ in range(_MAX_ROLLUP_DEPTH):
            info = self.by_code.get(cur) if cur is not None else None
            if info is None:
                break
            chain.append({"level": info["level"], "code": cur, "name": info["name"]})
            if info["parent"] is None:
                break
            cur = info["parent"]
        chain.reverse()
        return chain

    def ancestors_including(self, code: str | None) -> list[str]:
        """The code itself plus every ancestor up to the root (leaf→root)."""
        out: list[str] = []
        cur = code
        for _ in range(_MAX_ROLLUP_DEPTH):
            if cur is None or cur not in self.by_code:
                break
            out.append(cur)
            cur = self.by_code[cur]["parent"]
        return out

    def name(self, code: str | None) -> str | None:
        info = self.by_code.get(code) if code is not None else None
        return info["name"] if info else None

    def levels_summary(self) -> dict[int, int]:
        c = Counter(n["level"] for n in self.by_code.values() if n["level"] is not None)
        return dict(sorted(c.items()))


def load_tree(reader, system: str | None) -> SectorTree:
    return SectorTree(reader.read_sectors(classification_system=system).to_pylist())


def list_systems(reader) -> dict[str, int]:
    """{classification_system: node_count} across all sectors."""
    out: Counter = Counter()
    for r in reader.read_sectors().to_pylist():
        cs = r.get("classification_system")
        if cs is not None:
            out[cs] += 1
    return dict(out)


def rollup_symbols(tree: SectorTree, symbol_rows: list[dict], level: int):
    """Map each symbol to its industry code at ``level``.

    Returns ``(industry_of, covered, total)`` where ``industry_of`` is
    ``{symbol: industry_code}`` for the symbols that *could* be resolved, and
    covered/total drive the explicit coverage badge (§8.4: never silently drop).
    """
    industry_of: dict[str, str] = {}
    total = 0
    for r in symbol_rows:
        sym = r.get("symbol")
        if sym is None:
            continue
        total += 1
        ind = tree.roll(r.get("sector_code"), level)
        if ind is not None:
            industry_of[sym] = ind
    return industry_of, len(industry_of), total
