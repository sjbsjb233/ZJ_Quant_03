"""Structured degradation台账 (设计 §4).

A *degradation* is any moment where the engine could not honour the run
configuration to the letter but chose to continue (rather than abort). Each one
becomes a :class:`DegradationEvent` — a stable, machine-readable record of *what*
degraded, *why*, *how badly*, and *what to do about it* — and is collected into a
per-run :class:`DegradationLedger`.

The whole point (设计 §0): there must be **no silent degradation**. Every
``if <col> in df.columns: ...`` style skip in the engine is replaced by either a
hard block (preflight) or a ``ledger.record(...)`` call. A run with ≥1 event is
reported as ``status=degraded`` and surfaced prominently in the UI.

This module is pure data — it has no FastAPI / DB / DataReader dependency, so it
is importable from notebooks and the engine alike.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass, field


# ── severity ordering (for sorting / "worst" selection) ─────────────────────
SEVERITY_ORDER = {"critical": 3, "high": 2, "medium": 1, "low": 0}


@dataclass
class DegradationEvent:
    """One structured degradation record (设计 §4).

    ``code`` is a stable enum (see :data:`DEGRADATION_CODES` / 设计 §14) so the
    frontend and analytics can group/filter without parsing free text.
    """

    code: str                 # stable enum, e.g. "DG_IND_NEUTRAL_SKIPPED" / "PF_NEU_IND"
    category: str             # neutralization|universe|returns|preprocess|factor|window
    severity: str             # critical|high|medium|low
    stage: str                # preflight|prep|compute|...
    config_key: str | None    # related config knob, e.g. "neutralize_industry"
    title: str                # human-readable (中文)
    reason: str               # *why* it degraded (specific)
    impact: str               # effect on the result
    suggested_action: str     # what the operator should do
    evidence: dict = field(default_factory=dict)  # expected vs actual / coverage / sample
    policy: str = "degrade"   # the resolved policy that let this through (degrade|ignore)
    occurred_at: str = field(
        default_factory=lambda: dt.datetime.utcnow().isoformat() + "Z"
    )

    def to_jsonable(self) -> dict:
        return asdict(self)


class DegradationLedger:
    """Per-run台账 aggregating every :class:`DegradationEvent` (设计 §4)."""

    def __init__(self) -> None:
        self._events: list[DegradationEvent] = []

    # ── recording ──────────────────────────────────────────────────────────
    def record(self, event: DegradationEvent) -> None:
        self._events.append(event)

    # ── queries ────────────────────────────────────────────────────────────
    @property
    def events(self) -> list[DegradationEvent]:
        return list(self._events)

    @property
    def count(self) -> int:
        return len(self._events)

    @property
    def has_critical(self) -> bool:
        return any(e.severity == "critical" for e in self._events)

    def has_code(self, code: str) -> bool:
        """True if an event with this code is already recorded.

        Used to de-duplicate events that would otherwise fire once per
        rebalance day / per horizon (e.g. DG_PRICE_FALLBACK, DG_PREP_NOOP).
        """
        return any(e.code == code for e in self._events)

    def level(self) -> str:
        """Derived ``degradation_level`` (设计 §3): none|partial|severe."""
        if not self._events:
            return "none"
        return "severe" if self.has_critical else "partial"

    def to_jsonable(self) -> list[dict]:
        return [e.to_jsonable() for e in self._events]


# ── catalogue of known codes (设计 §14) — single source of truth for severity
# + category + default human copy so call sites stay short and consistent. ───
DEGRADATION_CODES: dict[str, dict] = {
    # preflight (PF_*)
    "PF_NEU_IND": {
        "category": "neutralization", "severity": "critical",
        "title": "行业中性化所需数据缺失",
        "config_key": "neutralize_industry",
    },
    "PF_NEU_SIZE": {
        "category": "neutralization", "severity": "critical",
        "title": "市值中性化所需数据缺失",
        "config_key": "neutralize_size",
    },
    "PF_RET_PRICE": {
        "category": "returns", "severity": "high",
        "title": "VWAP 收益口径所需数据缺失",
        "config_key": "return_price",
    },
    "PF_UNI_FLAG": {
        "category": "universe", "severity": "high",
        "title": "预设指数成分 flag 列缺失/全空",
        "config_key": "universe_id",
    },
    "PF_UNI_ST": {
        "category": "universe", "severity": "high",
        "title": "ST 状态数据缺失,无法剔除 ST",
        "config_key": "exclude_st",
    },
    "PF_UNI_SUSPEND": {
        "category": "universe", "severity": "high",
        "title": "停牌/可交易状态缺失,无法剔除停牌股",
        "config_key": "exclude_suspended",
    },
    "PF_UNI_LISTDAYS": {
        "category": "universe", "severity": "medium",
        "title": "上市天数缺失,无法做次新过滤",
        "config_key": "min_list_days",
    },
    "PF_PREP_METHOD": {
        "category": "preprocess", "severity": "medium",
        "title": "去极值/标准化方法名非法",
        "config_key": "winsorize",
    },
    "PF_WINDOW": {
        "category": "window", "severity": "high",
        "title": "调仓日不足/窗口对 horizon 不足",
        "config_key": "rebalance",
    },
    # runtime (DG_*)
    "DG_IND_NEUTRAL_SKIPPED": {
        "category": "neutralization", "severity": "critical",
        "title": "行业中性化未生效",
        "config_key": "neutralize_industry",
    },
    "DG_IND_PARTIAL": {
        "category": "neutralization", "severity": "high",
        "title": "部分标的行业未知被剔除",
        "config_key": "neutralize_industry",
    },
    "DG_SIZE_DROPPED": {
        "category": "neutralization", "severity": "high",
        "title": "缺市值导致标的/调仓日被剔除",
        "config_key": "neutralize_size",
    },
    "DG_PRICE_FALLBACK": {
        "category": "returns", "severity": "high",
        "title": "VWAP 收益口径回退为 CLOSE",
        "config_key": "return_price",
    },
    "DG_FIELD_FALLBACK": {
        "category": "factor", "severity": "medium",
        "title": "市值字段回退 (circ_mv → total_mv)",
        "config_key": "size_field",
    },
    "DG_UNI_FILTER_NOOP": {
        "category": "universe", "severity": "high",
        "title": "股票池过滤实际未生效",
        "config_key": "universe_id",
    },
    "DG_PREP_NOOP": {
        "category": "preprocess", "severity": "low",
        "title": "去极值/标准化因截面退化未生效",
        "config_key": "winsorize",
    },
    "DG_PREP_QBAD": {
        "category": "preprocess", "severity": "medium",
        "title": "分位去极值参数越界已回退",
        "config_key": "winsorize",
    },
}


def make_event(
    code: str,
    *,
    stage: str,
    reason: str,
    impact: str,
    suggested_action: str,
    evidence: dict | None = None,
    severity: str | None = None,
    title: str | None = None,
    category: str | None = None,
    config_key: str | None = None,
    policy: str = "degrade",
) -> DegradationEvent:
    """Build a :class:`DegradationEvent`, pulling defaults from the catalogue.

    Keeps call sites short: ``make_event("DG_IND_NEUTRAL_SKIPPED", stage="prep",
    reason=..., impact=..., suggested_action=..., evidence={...})``.
    """
    meta = DEGRADATION_CODES.get(code, {})
    return DegradationEvent(
        code=code,
        category=category or meta.get("category", "factor"),
        severity=severity or meta.get("severity", "medium"),
        stage=stage,
        config_key=config_key if config_key is not None else meta.get("config_key"),
        title=title or meta.get("title", code),
        reason=reason,
        impact=impact,
        suggested_action=suggested_action,
        evidence=dict(evidence or {}),
        policy=policy,
    )
