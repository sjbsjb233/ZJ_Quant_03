"""EvalConfig — every knob a user can flip when submitting a run (design §10).

The whole struct is JSON-serialized into FactorRun.run_config so reruns and
audit are deterministic.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable


@dataclass
class EvalConfig:
    # ── universe + window ────────────────────────────────────────────────
    universe_id: str = "preset:hs300"
    custom_symbols: list[str] | None = None  # for kind=custom
    start: dt.date | None = None
    end: dt.date | None = None
    rebalance: str = "W"  # "D" | "W" | "M" | "Nd"

    # ── return horizons & IC ─────────────────────────────────────────────
    horizons: tuple[int, ...] = (1, 5, 10, 20)
    primary_horizon: int = 5
    ic_type: str = "rank"  # "rank" | "pearson" | "both"
    return_price: str = "close"  # "close" | "vwap" | "open_next"

    # ── preprocess ───────────────────────────────────────────────────────
    winsorize: str = "mad"  # "none" | "mad" | "quantile"
    winsorize_n: float = 5.0  # MAD multiplier, or quantile in (0, 0.5]
    standardize: str = "zscore"  # "zscore" | "none"

    # ── neutralization (both supported per user request) ─────────────────
    neutralize_size: bool = True
    size_field: str = "circ_mv"  # "circ_mv" | "total_mv"
    neutralize_industry: bool = True
    classification_system: str = "SW"  # "SW" (申万) | "CITIC" (中信)
    industry_level: int = 1  # 1 | 2 | 3 — 申万一/二/三级

    # ── stats ────────────────────────────────────────────────────────────
    quantiles: int = 5
    compute_long_short: bool = True
    compute_turnover: bool = True
    compute_autocorr: bool = False
    layer_weight: str = "equal"  # "equal" | "cap"

    # ── lane ─────────────────────────────────────────────────────────────
    lane: str = "interactive"  # "interactive" | "routine"

    # ── trading cost (for turnover-after-cost calc) ──────────────────────
    cost_bps: float = 30.0  # round-trip bps per layer turnover

    # ── factor extras (for storage of param-schema params side-by-side) ──
    extras: dict[str, Any] = field(default_factory=dict)

    # ────────────────────────────────────────────────────────────────────
    def to_jsonable(self) -> dict[str, Any]:
        d = asdict(self)
        if d.get("start"):
            d["start"] = d["start"].isoformat()
        if d.get("end"):
            d["end"] = d["end"].isoformat()
        d["horizons"] = list(self.horizons)
        return d

    # 市值口径白名单。size_field 会流入数据层 SQL(preflight 聚合下推),且
    # ln_size 只认这两个值 —— 在请求体边界就拒绝其它输入,既给清晰报错也作为
    # 防 SQL 注入的第二道防线(安全审查 P1;首道在 preflight._safe_column)。
    SIZE_FIELDS = ("circ_mv", "total_mv")

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "EvalConfig":
        d = dict(d or {})
        # Date coercion
        if "start" in d and isinstance(d["start"], str):
            d["start"] = dt.date.fromisoformat(d["start"])
        if "end" in d and isinstance(d["end"], str):
            d["end"] = dt.date.fromisoformat(d["end"])
        if "horizons" in d and not isinstance(d["horizons"], tuple):
            d["horizons"] = tuple(int(x) for x in d["horizons"])
        if "size_field" in d and d["size_field"] not in cls.SIZE_FIELDS:
            raise ValueError(
                f"size_field 必须是 {cls.SIZE_FIELDS} 之一,收到 {d['size_field']!r}")
        # Strip extras (kept verbatim)
        known = {f.name for f in cls.__dataclass_fields__.values()}
        extras = {k: v for k, v in d.items() if k not in known}
        keep = {k: v for k, v in d.items() if k in known}
        keep.setdefault("extras", {}).update(extras)
        return cls(**keep)


# Convenience: an ID signature used to dedupe/cache (correlation cache, etc.)
def config_signature(cfg: EvalConfig, extra: Iterable[str] = ()) -> str:
    import hashlib
    parts = [
        cfg.universe_id,
        ",".join(cfg.custom_symbols or []),
        cfg.start.isoformat() if cfg.start else "",
        cfg.end.isoformat() if cfg.end else "",
        cfg.rebalance,
        ",".join(str(h) for h in cfg.horizons),
        str(cfg.primary_horizon),
        cfg.ic_type,
        cfg.return_price,
        cfg.winsorize,
        str(cfg.winsorize_n),
        cfg.standardize,
        f"{cfg.neutralize_size}:{cfg.size_field}",
        f"{cfg.neutralize_industry}:{cfg.classification_system}:{cfg.industry_level}",
        str(cfg.quantiles),
        str(cfg.compute_long_short),
        str(cfg.compute_turnover),
        cfg.layer_weight,
        *list(extra),
    ]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]
