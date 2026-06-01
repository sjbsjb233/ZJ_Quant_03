"""Universe Selector — resolve a UniverseDef into per-day symbol sets.

The factor service stores Universe rows in its own SQLite; this module is
agnostic about the storage (the service hands it a dict-like UniverseDef).

Preset indexes are resolved against the data layer's `raw.universe` flags
(in_hs300 / in_zz500 / in_zz1000 / in_sse50). "全市场" = no flag filter.
"中证 800" = in_hs300 ∪ in_zz500 (per design §9.1).

Custom pools are constant symbol sets plus the usual ST/suspended/min-list
filters applied per-day.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Iterable

import polars as pl

from .degradation import make_event


# ─────────────────────────────────────────────────────────────────────────────
# Preset universes (built into the system, no UI to delete)
# ─────────────────────────────────────────────────────────────────────────────
# preset_id -> (display name, flag(s) for resolve)
# flag: None = all-market; str = single column; tuple[str,...] = OR of columns
PRESET_FLAG: dict[str, str | tuple[str, ...] | None] = {
    "preset:all": None,
    "preset:hs300": "in_hs300",
    "preset:zz500": "in_zz500",
    "preset:zz800": ("in_hs300", "in_zz500"),  # 中证 800 = 300 ∪ 500
    "preset:zz1000": "in_zz1000",
    "preset:sse50": "in_sse50",
}

PRESET_UNIVERSES: list[dict] = [
    {
        "universe_id": "preset:all",
        "name": "全市场",
        "kind": "preset_index",
        "preset_flag": None,
        "classification_system": "SW",
        "default_industry_level": 1,
        "snapshot_retention_days": 730,
        "exclude_st": True,
        "exclude_suspended": True,
        "min_list_days": 60,
        "owner": "system",
    },
    {
        "universe_id": "preset:hs300",
        "name": "沪深 300",
        "kind": "preset_index",
        "preset_flag": "in_hs300",
        "classification_system": "SW",
        "default_industry_level": 1,
        "snapshot_retention_days": 730,
        "exclude_st": False,
        "exclude_suspended": True,
        "min_list_days": 0,
        "owner": "system",
    },
    {
        "universe_id": "preset:zz500",
        "name": "中证 500",
        "kind": "preset_index",
        "preset_flag": "in_zz500",
        "classification_system": "SW",
        "default_industry_level": 1,
        "snapshot_retention_days": 730,
        "exclude_st": False,
        "exclude_suspended": True,
        "min_list_days": 0,
        "owner": "system",
    },
    {
        "universe_id": "preset:zz800",
        "name": "中证 800",
        "kind": "preset_index",
        "preset_flag": "in_hs300|in_zz500",  # stored as literal; resolve maps to tuple
        "classification_system": "SW",
        "default_industry_level": 1,
        "snapshot_retention_days": 730,
        "exclude_st": False,
        "exclude_suspended": True,
        "min_list_days": 0,
        "owner": "system",
    },
    {
        "universe_id": "preset:zz1000",
        "name": "中证 1000",
        "kind": "preset_index",
        "preset_flag": "in_zz1000",
        "classification_system": "SW",
        "default_industry_level": 1,
        "snapshot_retention_days": 730,
        "exclude_st": False,
        "exclude_suspended": True,
        "min_list_days": 0,
        "owner": "system",
    },
    {
        "universe_id": "preset:sse50",
        "name": "上证 50",
        "kind": "preset_index",
        "preset_flag": "in_sse50",
        "classification_system": "SW",
        "default_industry_level": 1,
        "snapshot_retention_days": 730,
        "exclude_st": False,
        "exclude_suspended": True,
        "min_list_days": 0,
        "owner": "system",
    },
]


# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class UniverseDef:
    """In-memory adapter so resolve_universe can take either a dict or this."""

    universe_id: str
    kind: str  # "preset_index" | "custom"
    preset_flag: str | tuple[str, ...] | None = None
    classification_system: str = "SW"
    default_industry_level: int = 1
    exclude_st: bool = True
    exclude_suspended: bool = True
    min_list_days: int = 0
    snapshot_retention_days: int = 730

    @classmethod
    def from_dict(cls, d: dict) -> "UniverseDef":
        flag = d.get("preset_flag")
        # Stored "in_hs300|in_zz500" -> tuple
        if isinstance(flag, str) and "|" in flag:
            flag = tuple(p.strip() for p in flag.split("|") if p.strip())
        return cls(
            universe_id=d["universe_id"],
            kind=d.get("kind", "preset_index"),
            preset_flag=flag,
            classification_system=d.get("classification_system") or "SW",
            default_industry_level=int(d.get("default_industry_level") or 1),
            exclude_st=bool(d.get("exclude_st", True)),
            exclude_suspended=bool(d.get("exclude_suspended", True)),
            min_list_days=int(d.get("min_list_days") or 0),
            snapshot_retention_days=int(d.get("snapshot_retention_days") or 730),
        )


def _resolve_flag(flag):
    if flag is None:
        return None
    if isinstance(flag, str) and "|" in flag:
        return tuple(p.strip() for p in flag.split("|") if p.strip())
    return flag


# ─────────────────────────────────────────────────────────────────────────────
def resolve_universe(
    reader,
    uni_def: dict | UniverseDef,
    custom_symbols: Iterable[str] | None,
    start: dt.date,
    end: dt.date,
    ledger=None,
) -> pl.DataFrame:
    """Return per-day [date, symbol] for the evaluation cross-section pool.

    Preset universes are inherently time-varying (the data layer's `universe`
    table carries daily flags), so the result is PIT-correct out of the box.
    Custom universes overlay (ST / suspended / min_list_days) filters per-day.
    """
    if isinstance(uni_def, UniverseDef):
        d = uni_def.__dict__
    else:
        d = uni_def

    uni = pl.from_arrow(reader.read_universe(start=start, end=end))
    if uni.height == 0:
        return pl.DataFrame(
            {"date": [], "symbol": []},
            schema={"date": pl.Date, "symbol": pl.Utf8},
        )

    kind = d.get("kind", "preset_index")
    if kind == "preset_index":
        flag = _resolve_flag(d.get("preset_flag"))
        if flag is None:
            pass  # all market — no flag filter
        elif isinstance(flag, tuple):
            existing = [c for c in flag if c in uni.columns]
            if existing:
                expr = pl.col(existing[0]) == True  # noqa: E712
                for c in existing[1:]:
                    expr = expr | (pl.col(c) == True)  # noqa: E712
                uni = uni.filter(expr)
        else:
            if flag in uni.columns:
                uni = uni.filter(pl.col(flag) == True)  # noqa: E712
    else:  # custom
        syms = list(custom_symbols or [])
        if not syms:
            return uni.head(0).select(["date", "symbol"])
        uni = uni.filter(pl.col("symbol").is_in(syms))

    # Universal overlay filters (apply to both preset + custom).
    # No-silent-degradation (设计 §6): a filter that was *requested* but whose
    # backing column is missing or entirely null is a NO-OP — the ST/suspended
    # stocks it was meant to remove stay in the pool. Record DG_UNI_FILTER_NOOP.
    def _noop(which: str, columns: list[str], impact: str, action: str) -> None:
        if ledger is None:
            return
        ledger.record(
            make_event(
                "DG_UNI_FILTER_NOOP",
                stage="prep",
                reason=f"股票池过滤「{which}」无法生效:列 {columns} 缺失或全空",
                impact=impact,
                suggested_action=action,
                config_key="universe_id",
                evidence={"filter": which, "columns": columns},
            )
        )

    if d.get("exclude_st", True):
        if "st_status" in uni.columns and uni["st_status"].drop_nulls().len() > 0:
            uni = uni.filter(
                (pl.col("st_status").is_null()) | (pl.col("st_status") == "normal")
            )
        else:
            _noop("剔除 ST", ["st_status"],
                  "ST 股未被剔除,可能污染回测", "补齐 raw.universe.st_status 后重跑")
    if d.get("exclude_suspended", True):
        has_susp = "is_suspended" in uni.columns and uni["is_suspended"].drop_nulls().len() > 0
        has_trad = "is_tradable" in uni.columns and uni["is_tradable"].drop_nulls().len() > 0
        if has_susp:
            uni = uni.filter(pl.col("is_suspended") == False)  # noqa: E712
        if has_trad:
            uni = uni.filter(pl.col("is_tradable") == True)  # noqa: E712
        if not (has_susp or has_trad):
            _noop("剔除停牌", ["is_suspended", "is_tradable"],
                  "停牌股未被剔除,换手/收益可能失真", "补齐停牌状态列后重跑")
    mn = int(d.get("min_list_days") or 0)
    if mn > 0:
        if "listed_days" in uni.columns and uni["listed_days"].drop_nulls().len() > 0:
            uni = uni.filter(
                pl.col("listed_days").is_not_null() & (pl.col("listed_days") >= mn)
            )
        else:
            _noop("次新过滤", ["listed_days"],
                  "次新股未被剔除,可能纳入上市不足天数的标的", "补齐 raw.universe.listed_days 后重跑")

    return uni.select(["date", "symbol"]).unique().sort(["date", "symbol"])


def rebalance_dates(uni: pl.DataFrame, rebalance: str) -> list[dt.date]:
    """Convert universe per-day index into evaluation rebalance dates.

    `rebalance`:
      - "D"  : every available date
      - "W"  : every 5 trading days (approx weekly)
      - "M"  : ~21 trading days
      - "Nd" : every N trading days (e.g. "3d", "10d")
    """
    if uni.height == 0:
        return []
    days = sorted(set(uni["date"].to_list()))
    rb = (rebalance or "W").lower().strip()
    if rb == "d":
        step = 1
    elif rb == "w":
        step = 5
    elif rb == "m":
        step = 21
    elif rb.endswith("d") and rb[:-1].isdigit():
        step = max(1, int(rb[:-1]))
    else:
        step = 5
    return days[::step]
