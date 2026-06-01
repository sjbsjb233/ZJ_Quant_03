"""运行前置全查 — Preflight Gate (设计 §5).

Before a single number is computed, every *enabled* feature in the run config is
checked against the data that is actually present. A check yields one of:

  - ``pass``    : the data needed for this feature is there → proceed silently.
  - ``degrade`` : the data is missing/insufficient, but the resolved policy lets
                  the run continue → a :class:`DegradationEvent` is recorded and
                  the run will end ``status=degraded``.
  - ``block``   : the resolved policy forbids running without this data →
                  :class:`PreflightBlocked` is raised, the run never computes and
                  is marked ``status=failed``.

The *固有严重度* (severity) of a check is intrinsic; the *处置策略* (policy —
block/degrade/ignore) is configurable via a profile (``strict`` default for
finance) plus per-check overrides from ``run_config.degradation_policy``.

Design deviation note: the spec's ``run(self, reader, cfg, symbols, start, end)``
omits the *universe definition*, yet the PF_UNI_* checks need the universe's
``exclude_st`` / ``preset_flag`` etc. (which live on the Universe row, not on
EvalConfig). We therefore pass a small :class:`PreflightContext` bundling reader,
cfg, the universe def, symbols and window — and it memoises the (potentially
heavy) ``read_bars`` / ``read_universe`` calls so the whole gate touches each
table at most once.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Protocol

import polars as pl

from .degradation import DEGRADATION_CODES, DegradationLedger, make_event
from .neutralize import industry_at_level
from .universe import UniverseDef, _resolve_flag, rebalance_dates


# ── coverage / window thresholds (设计 §12.4 — centralised, no code edits to tune)
PREFLIGHT_THRESHOLDS: dict[str, float] = {
    "PF_NEU_IND": 0.95,    # industry classification coverage
    "PF_NEU_SIZE": 0.95,   # size_field coverage
    "PF_RET_PRICE": 0.90,  # vwap coverage
    "PF_WINDOW_MIN_REBAL": 2,  # minimum rebalance dates
}


# ── policy profiles (设计 §5.2 / §12.3) ─────────────────────────────────────
STRICT_PROFILE: dict[str, str] = {
    "PF_NEU_IND": "block",
    "PF_NEU_SIZE": "block",
    "PF_RET_PRICE": "block",
    "PF_UNI_FLAG": "block",
    "PF_UNI_ST": "degrade",
    "PF_UNI_SUSPEND": "degrade",
    "PF_UNI_LISTDAYS": "degrade",
    "PF_PREP_METHOD": "block",
    "PF_WINDOW": "block",
}

# Permissive 灰度档 (设计 §12.3): never block on *missing data* — record & run on.
# PF_PREP_METHOD (illegal enum) and PF_WINDOW (nothing to compute at all) stay
# block in every profile: there is no valid degraded behaviour for them.
PERMISSIVE_PROFILE: dict[str, str] = {
    "PF_NEU_IND": "degrade",
    "PF_NEU_SIZE": "degrade",
    "PF_RET_PRICE": "degrade",
    "PF_UNI_FLAG": "degrade",
    "PF_UNI_ST": "degrade",
    "PF_UNI_SUSPEND": "degrade",
    "PF_UNI_LISTDAYS": "degrade",
    "PF_PREP_METHOD": "block",
    "PF_WINDOW": "block",
}

_VALID_WINSORIZE = {"none", "mad", "quantile"}
_VALID_STANDARDIZE = {"none", "zscore"}
_VALID_POLICIES = {"block", "degrade", "ignore"}


# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class CheckResult:
    check_id: str
    status: str                       # "pass" | "degrade" | "block" | "skipped"
    event: Any | None = None          # DegradationEvent when not pass
    # Display-only side data for the 运行透视 checklist (coverage/threshold on a
    # *pass*, applies flag on a *skip*, human cov_text). Never used by the gate.
    info: dict = field(default_factory=dict)


class PreflightBlocked(Exception):
    """Carries every ``block``-hit CheckResult so the orchestration layer can
    persist the structured 阻断详情 and return it to the frontend."""

    def __init__(self, results: list[CheckResult]):
        self.results = results
        super().__init__(f"preflight blocked by {[r.check_id for r in results]}")


# ─────────────────────────────────────────────────────────────────────────────
class PreflightContext:
    """Everything a check needs + memoised data accessors.

    ``bars()`` / ``universe()`` are read at most once across all checks.
    """

    def __init__(self, reader, cfg, uni_def, symbols, start, end):
        self.reader = reader
        self.cfg = cfg
        self.symbols = list(symbols or [])
        self.start = start
        self.end = end
        # Normalise the universe def to a plain dict regardless of input shape.
        if isinstance(uni_def, UniverseDef):
            self.uni: dict = dict(uni_def.__dict__)
        else:
            self.uni = dict(uni_def or {})
        self._bars: pl.DataFrame | None = None
        self._universe: pl.DataFrame | None = None

    def bars(self) -> pl.DataFrame:
        if self._bars is None:
            tbl = self.reader.read_bars_daily(
                symbols=self.symbols or None,
                start=self.start,
                end=self.end,
                adjust="none",
            )
            self._bars = pl.from_arrow(tbl)
        return self._bars

    def universe(self) -> pl.DataFrame:
        if self._universe is None:
            tbl = self.reader.read_universe(start=self.start, end=self.end)
            self._universe = pl.from_arrow(tbl)
        return self._universe


# ── coverage helpers ────────────────────────────────────────────────────────
def _field_symbol_coverage(
    df: pl.DataFrame, symbols: list[str], field: str, *, positive: bool
) -> tuple[float, int, int, list[str]]:
    """Fraction of `symbols` that have ≥1 usable value in `field`.

    `positive=True` additionally requires value > 0 (market-cap fields).
    Returns (coverage, n_symbols, n_have, sample_missing).
    """
    n = len(symbols)
    if n == 0:
        return 0.0, 0, 0, []
    if df.height == 0 or field not in df.columns:
        return 0.0, n, 0, list(symbols[:5])
    cond = pl.col(field).is_not_null()
    if positive:
        cond = cond & (pl.col(field) > 0)
    have = df.filter(cond).select("symbol").unique()["symbol"].to_list()
    have_set = set(have)
    missing = [s for s in symbols if s not in have_set]
    cov = len(have_set) / n
    return cov, n, len(have_set), missing[:8]


# ─────────────────────────────────────────────────────────────────────────────
# Check protocol + concrete checks
# ─────────────────────────────────────────────────────────────────────────────
class PreflightCheck(Protocol):
    id: str
    severity: str
    default_policy: str

    def applies(self, ctx: PreflightContext) -> bool: ...
    def run(self, ctx: PreflightContext) -> CheckResult: ...


class _IndustryCoverageCheck:
    id = "PF_NEU_IND"
    severity = "critical"
    default_policy = "block"

    def applies(self, ctx: PreflightContext) -> bool:
        return bool(ctx.cfg.neutralize_industry)

    def run(self, ctx: PreflightContext) -> CheckResult:
        thr = PREFLIGHT_THRESHOLDS["PF_NEU_IND"]
        n = len(ctx.symbols)
        ind = industry_at_level(
            ctx.reader, ctx.symbols, ctx.cfg.classification_system, ctx.cfg.industry_level
        )
        usable = ind.filter(pl.col("industry").is_not_null())["symbol"].to_list()
        n_have = len(usable)
        cov = (n_have / n) if n else 0.0
        if cov >= thr:
            return CheckResult(self.id, "pass", info={
                "coverage": round(cov, 4), "threshold": thr,
                "cov_text": f"{cov:.1%} ≥ {thr:.0%}",
            })
        missing = ind.filter(pl.col("industry").is_null())["symbol"].to_list()
        ev = make_event(
            self.id,
            stage="preflight",
            reason=(
                f"{ctx.cfg.classification_system} {ctx.cfg.industry_level}级行业分类"
                f"可解析覆盖率 {cov:.1%} < 阈值 {thr:.0%}"
                f"(可解析 {n_have} / {n})"
            ),
            impact="行业中性化将无法生效或仅对少数标的生效,残差近似仅去均值",
            suggested_action="导入对应行业分类数据(reference.sectors)并确认 symbols.sector_code 已填充后重跑",
            evidence={
                "expected": f"{ctx.cfg.classification_system} 行业覆盖 ≥ {thr:.0%}",
                "actual": f"可解析行业的股票 {n_have} / {n}",
                "coverage": round(cov, 4),
                "threshold": thr,
                "affected_symbols": n - n_have,
                "sample": missing[:8],
            },
        )
        return CheckResult(self.id, "degrade", ev)


class _SizeCoverageCheck:
    id = "PF_NEU_SIZE"
    severity = "critical"
    default_policy = "block"

    def applies(self, ctx: PreflightContext) -> bool:
        return bool(ctx.cfg.neutralize_size)

    def run(self, ctx: PreflightContext) -> CheckResult:
        thr = PREFLIGHT_THRESHOLDS["PF_NEU_SIZE"]
        field = ctx.cfg.size_field
        cov, n, n_have, missing = _field_symbol_coverage(
            ctx.bars(), ctx.symbols, field, positive=True
        )
        if cov >= thr:
            return CheckResult(self.id, "pass", info={
                "coverage": round(cov, 4), "threshold": thr,
                "cov_text": f"{cov:.1%} ≥ {thr:.0%}",
            })
        ev = make_event(
            self.id,
            stage="preflight",
            reason=f"{field} 非空覆盖率 {cov:.1%} < 阈值 {thr:.0%}(有值 {n_have} / {n})",
            impact="缺市值的标的/调仓日将在回归掩码中被剔除,覆盖度缩水,中性化口径失真",
            suggested_action=f"补齐 raw.bars_daily.{field} 后重跑;或改用另一市值口径",
            evidence={
                "expected": f"{field} 覆盖 ≥ {thr:.0%}",
                "actual": f"有市值的股票 {n_have} / {n}",
                "coverage": round(cov, 4),
                "threshold": thr,
                "affected_symbols": n - n_have,
                "sample": missing,
            },
        )
        return CheckResult(self.id, "degrade", ev)


class _VwapCoverageCheck:
    id = "PF_RET_PRICE"
    severity = "high"
    default_policy = "block"

    def applies(self, ctx: PreflightContext) -> bool:
        return ctx.cfg.return_price == "vwap"

    def run(self, ctx: PreflightContext) -> CheckResult:
        thr = PREFLIGHT_THRESHOLDS["PF_RET_PRICE"]
        cov, n, n_have, missing = _field_symbol_coverage(
            ctx.bars(), ctx.symbols, "vwap", positive=True
        )
        if cov >= thr:
            return CheckResult(self.id, "pass", info={
                "coverage": round(cov, 4), "threshold": thr,
                "cov_text": f"{cov:.1%} ≥ {thr:.0%}",
            })
        ev = make_event(
            self.id,
            stage="preflight",
            reason=f"vwap 非空覆盖率 {cov:.1%} < 阈值 {thr:.0%}(有值 {n_have} / {n})",
            impact="VWAP 收益口径将静默回退为 CLOSE,实际收益口径与配置不符",
            suggested_action="补齐 raw.bars_daily.vwap 后重跑;或显式改用 close 口径",
            evidence={
                "expected": f"vwap 覆盖 ≥ {thr:.0%}",
                "actual": f"有 vwap 的股票 {n_have} / {n}",
                "coverage": round(cov, 4),
                "threshold": thr,
                "affected_symbols": n - n_have,
                "sample": missing,
            },
        )
        return CheckResult(self.id, "degrade", ev)


class _UniverseFlagCheck:
    id = "PF_UNI_FLAG"
    severity = "high"
    default_policy = "block"

    def applies(self, ctx: PreflightContext) -> bool:
        if ctx.uni.get("kind") != "preset_index":
            return False
        return _resolve_flag(ctx.uni.get("preset_flag")) is not None

    def run(self, ctx: PreflightContext) -> CheckResult:
        flag = _resolve_flag(ctx.uni.get("preset_flag"))
        cols = list(flag) if isinstance(flag, tuple) else [flag]
        uni = ctx.universe()
        present = [c for c in cols if c in uni.columns]
        n_true = 0
        if present and uni.height > 0:
            expr = pl.col(present[0]) == True  # noqa: E712
            for c in present[1:]:
                expr = expr | (pl.col(c) == True)  # noqa: E712
            n_true = uni.filter(expr).height
        if present and n_true > 0:
            return CheckResult(self.id, "pass", info={
                "cov_text": f"成分 flag 命中 {n_true} 行",
            })
        ev = make_event(
            self.id,
            stage="preflight",
            reason=(
                f"指数成分 flag 列 {cols} "
                + ("不存在于 universe 表" if not present else "全为空/全 False")
            ),
            impact="预设指数股票池将解析为空或全市场,评估范围与配置不符",
            suggested_action="补齐 raw.universe 的指数成分 flag 列后重跑",
            evidence={
                "expected": f"{cols} 至少一列存在且有 True 值",
                "actual": f"存在列 {present};True 行数 {n_true}",
                "affected_symbols": len(ctx.symbols),
            },
        )
        return CheckResult(self.id, "degrade", ev)


class _UniverseColumnCheck:
    """Shared body for the ST / suspended / list-days status-column checks."""

    id = ""
    severity = "high"
    default_policy = "degrade"
    config_key = ""
    columns: tuple[str, ...] = ()
    label = ""

    def _enabled(self, ctx: PreflightContext) -> bool:
        return bool(ctx.uni.get(self.config_key))

    def applies(self, ctx: PreflightContext) -> bool:
        return self._enabled(ctx)

    def run(self, ctx: PreflightContext) -> CheckResult:
        uni = ctx.universe()
        have = None
        for c in self.columns:
            if c in uni.columns and uni.filter(pl.col(c).is_not_null()).height > 0:
                have = c
                break
        if have is not None:
            return CheckResult(self.id, "pass", info={
                "cov_text": f"{self.label}状态列存在且非空",
            })
        ev = make_event(
            self.id,
            stage="preflight",
            reason=f"{self.label}状态列 {list(self.columns)} 缺失或全空",
            impact=f"{self.label}过滤将无法生效,污染股票池(可能纳入不该纳入的标的)",
            suggested_action=f"补齐 raw.universe 的 {list(self.columns)} 列后重跑",
            evidence={
                "expected": f"{list(self.columns)} 至少一列非全空",
                "actual": "全部缺失或全空",
                "affected_symbols": len(ctx.symbols),
            },
        )
        return CheckResult(self.id, "degrade", ev)


class _UniverseStCheck(_UniverseColumnCheck):
    id = "PF_UNI_ST"
    config_key = "exclude_st"
    columns = ("st_status",)
    label = "ST"


class _UniverseSuspendCheck(_UniverseColumnCheck):
    id = "PF_UNI_SUSPEND"
    config_key = "exclude_suspended"
    columns = ("is_suspended", "is_tradable")
    label = "停牌/可交易"


class _UniverseListDaysCheck(_UniverseColumnCheck):
    id = "PF_UNI_LISTDAYS"
    severity = "medium"
    config_key = "min_list_days"
    columns = ("listed_days",)
    label = "上市天数"

    def _enabled(self, ctx: PreflightContext) -> bool:
        return int(ctx.uni.get("min_list_days") or 0) > 0


class _PreprocMethodCheck:
    id = "PF_PREP_METHOD"
    severity = "medium"
    default_policy = "block"

    def applies(self, ctx: PreflightContext) -> bool:
        return True

    def run(self, ctx: PreflightContext) -> CheckResult:
        w, s = ctx.cfg.winsorize, ctx.cfg.standardize
        bad = []
        if w not in _VALID_WINSORIZE:
            bad.append(f"winsorize={w!r}")
        if s not in _VALID_STANDARDIZE:
            bad.append(f"standardize={s!r}")
        if not bad:
            return CheckResult(self.id, "pass", info={
                "cov_text": f"winsorize={w} · standardize={s}",
            })
        ev = make_event(
            self.id,
            stage="preflight",
            reason="预处理方法名非法: " + ", ".join(bad),
            impact="所选去极值/标准化方法不存在,无法按配置执行预处理",
            suggested_action=(
                f"winsorize 取 {sorted(_VALID_WINSORIZE)};"
                f"standardize 取 {sorted(_VALID_STANDARDIZE)}"
            ),
            evidence={
                "expected": {
                    "winsorize": sorted(_VALID_WINSORIZE),
                    "standardize": sorted(_VALID_STANDARDIZE),
                },
                "actual": {"winsorize": w, "standardize": s},
            },
        )
        return CheckResult(self.id, "degrade", ev)


class _WindowCheck:
    id = "PF_WINDOW"
    severity = "high"
    default_policy = "block"

    def applies(self, ctx: PreflightContext) -> bool:
        return True

    def run(self, ctx: PreflightContext) -> CheckResult:
        uni = ctx.universe()
        n_days = uni.select("date").unique().height if uni.height else 0
        rebal = rebalance_dates(uni, ctx.cfg.rebalance) if uni.height else []
        n_rebal = len(rebal)
        max_h = max(ctx.cfg.horizons) if ctx.cfg.horizons else 1
        min_rebal = int(PREFLIGHT_THRESHOLDS["PF_WINDOW_MIN_REBAL"])
        problems = []
        if n_rebal < min_rebal:
            problems.append(f"调仓日仅 {n_rebal} < {min_rebal}")
        if n_days <= max_h:
            problems.append(f"窗口交易日 {n_days} ≤ 最大 horizon {max_h},前向收益全缺")
        if not problems:
            return CheckResult(self.id, "pass", info={
                "cov_text": f"{n_rebal} 调仓日 ≥ {min_rebal}",
            })
        ev = make_event(
            self.id,
            stage="preflight",
            reason="; ".join(problems),
            impact="无足够调仓日/前向窗口,IC 与分层回测无法得到有效结果",
            suggested_action="扩大评估区间 [start, end],或降低调仓频率 / 缩短 horizon",
            evidence={
                "expected": f"调仓日 ≥ {min_rebal} 且 窗口交易日 > {max_h}",
                "actual": f"调仓日 {n_rebal};窗口交易日 {n_days}",
                "trading_days": n_days,
                "rebalance_dates": n_rebal,
                "max_horizon": max_h,
            },
        )
        return CheckResult(self.id, "degrade", ev)


# Registry — order is the order checks run / are reported (设计 §5.1).
REGISTRY: list[PreflightCheck] = [
    _PreprocMethodCheck(),
    _WindowCheck(),
    _IndustryCoverageCheck(),
    _SizeCoverageCheck(),
    _VwapCoverageCheck(),
    _UniverseFlagCheck(),
    _UniverseStCheck(),
    _UniverseSuspendCheck(),
    _UniverseListDaysCheck(),
]


# ─────────────────────────────────────────────────────────────────────────────
def evaluate_checks(
    reader,
    cfg,
    uni_def,
    symbols,
    start,
    end,
    *,
    profile: str = "strict",
    policy_overrides: dict[str, str] | None = None,
    include_pass: bool = False,
) -> tuple[list[CheckResult], dict[str, str]]:
    """Run every applicable check and return (results, effective policy).

    This is the shared core used by both :func:`run_preflight` (the gate) and the
    dry-run ``POST /runs/preflight`` endpoint. It does *not* raise — it classifies
    each non-pass result by the resolved policy and stamps the event's ``policy``.

    ``include_pass=False`` (default · gate/dry-run behaviour): only the *non-pass*
    results are returned, unchanged.

    ``include_pass=True`` (运行透视 §2.3c): the *full* checklist is returned —
    every check in :data:`REGISTRY`, including ``pass`` items (with their
    coverage/threshold ``info``) and not-applicable items as ``status="skipped"``.
    The gate logic still only acts on ``status == "block"`` and seeds the ledger
    from events, so passing this flag does not change what blocks/degrades.
    """
    base = PERMISSIVE_PROFILE if profile == "permissive" else STRICT_PROFILE
    policy = {**base, **(policy_overrides or {})}
    ctx = PreflightContext(reader, cfg, uni_def, symbols, start, end)

    out: list[CheckResult] = []
    for check in REGISTRY:
        try:
            applies = check.applies(ctx)
            if not applies:
                if include_pass:
                    out.append(CheckResult(check.id, "skipped", info={"applies": False}))
                continue
            res = check.run(ctx)
        except Exception as e:  # noqa: BLE001 — a check crashing must not crash the run
            # Treat an internal check error as a degrade-level signal so it is
            # visible rather than swallowed.
            ev = make_event(
                getattr(check, "id", "PF_UNKNOWN"),
                stage="preflight",
                reason=f"前置检查内部异常: {type(e).__name__}: {e}",
                impact="该检查未能完成,无法确认对应前提是否满足",
                suggested_action="检查数据层连通性与数据完整性",
                severity="medium",
                evidence={"error": f"{type(e).__name__}: {e}"},
            )
            out.append(CheckResult(getattr(check, "id", "PF_UNKNOWN"), "degrade", ev))
            continue
        if res.status == "pass":
            if include_pass:
                out.append(res)
            continue
        eff = policy.get(check.id, check.default_policy)
        # Clamp to a known policy — an invalid override (e.g. a typo in
        # run_config.degradation_policy) must NOT make a non-pass result vanish
        # from would_block/would_degrade; fall back to the check's default.
        if eff not in _VALID_POLICIES:
            eff = check.default_policy
        if res.event is not None:
            res.event.policy = eff
        res.status = eff  # "block" | "degrade" | "ignore"
        out.append(res)
    return out, policy


def run_preflight(
    reader,
    cfg,
    uni_def,
    symbols,
    start,
    end,
    *,
    profile: str = "strict",
    policy_overrides: dict[str, str] | None = None,
    trace=None,
) -> tuple[list[CheckResult], DegradationLedger]:
    """The gate (设计 §5.2).

    Returns ``([], ledger)`` when nothing blocked; ``ledger`` carries the
    degrade-policy + ignore-policy events to seed the run's台账.

    Raises :class:`PreflightBlocked` (carrying every block-hit result) the moment
    any check resolves to ``block`` — so a blocked run **never computes**.

    ``trace`` (运行透视 §2.3c): when supplied, the *full* checklist (含通过/跳过项)
    is recorded into the trace's ``preflight`` stage detail *before* the gate
    decision — so even a blocked run shows its complete体检清单. The full-list
    capture never changes the block/degrade outcome.
    """
    include_pass = trace is not None
    results, policy = evaluate_checks(
        reader, cfg, uni_def, symbols, start, end,
        profile=profile, policy_overrides=policy_overrides,
        include_pass=include_pass,
    )
    if trace is not None:
        rec = trace.stage_record("preflight") or trace.stage("preflight")
        rec.detail["checks"] = [_check_to_trace(r, policy) for r in results]
        trace.meta["preflight_profile"] = profile
        trace.meta["preflight_n_symbols"] = len(symbols or [])
    ledger = DegradationLedger()
    blocked: list[CheckResult] = []
    for res in results:
        if res.status == "block":
            blocked.append(res)
        elif res.status in ("pass", "skipped"):
            # full-list extras carry no event — they never touch the ledger.
            continue
        else:
            # "degrade" and "ignore" both seed the ledger; ignore keeps its
            # policy stamp so the audit shows it was deliberately let through.
            if res.event is not None:
                ledger.record(res.event)
    if blocked:
        raise PreflightBlocked(blocked)
    return [], ledger


def _check_to_trace(res: CheckResult, policy_map: dict[str, str]) -> dict:
    """Flatten a CheckResult for the 运行透视 checklist (含通过/跳过项展示字段).

    Extends :func:`check_result_to_json` with the pass/skip display fields the
    block-detail view never needed: ``applies`` / ``coverage`` / ``threshold`` /
    ``cov_text`` / resolved ``policy`` / catalogue ``severity``.
    """
    info = res.info or {}
    ev = res.event
    meta = DEGRADATION_CODES.get(res.check_id, {})
    applies = bool(info.get("applies", res.status != "skipped"))
    coverage = info.get("coverage")
    threshold = info.get("threshold")
    if ev is not None:
        e = ev.evidence or {}
        if coverage is None:
            coverage = e.get("coverage")
        if threshold is None:
            threshold = e.get("threshold")
    out = {
        "check_id": res.check_id,
        "status": res.status,                       # pass|degrade|block|ignore|skipped
        "applies": applies,
        "severity": (ev.severity if ev is not None else meta.get("severity")),
        "title": (ev.title if ev is not None else meta.get("title", res.check_id)),
        "config_key": (ev.config_key if ev is not None else meta.get("config_key")),
        "category": (ev.category if ev is not None else meta.get("category")),
        "policy": policy_map.get(res.check_id),
        "coverage": coverage,
        "threshold": threshold,
        "cov_text": info.get("cov_text"),
    }
    if ev is not None:
        out.update({
            "reason": ev.reason,
            "impact": ev.impact,
            "suggested_action": ev.suggested_action,
            "evidence": ev.evidence,
        })
    return out


def check_result_to_json(res: CheckResult) -> dict:
    """Flatten a CheckResult for API/DB (设计 §10.2 would_block/would_degrade)."""
    ev = res.event
    base = {
        "check_id": res.check_id,
        "status": res.status,
    }
    if ev is not None:
        base.update(
            {
                "severity": ev.severity,
                "category": ev.category,
                "config_key": ev.config_key,
                "stage": ev.stage,
                "title": ev.title,
                "reason": ev.reason,
                "impact": ev.impact,
                "suggested_action": ev.suggested_action,
                "evidence": ev.evidence,
                "policy": ev.policy,
            }
        )
    return base
