"""基线与对比算法 (资源画像方案 §5).

两级签名 (§5.1):
  - 精确基线 ``factor_id@version | universe_id | freq | window_bucket``
  - 同类基线 任务类 (``factor.eval.daily`` 等)
对比一律基于归一化指标 (``peak_per_mrow`` / ``cpu_sec_per_mrow``);``shared`` 样本
只与 ``shared`` 比, ``exact`` 只与 ``exact`` 比 (混比会系统性高估并发期样本)。

基线更新 (§5.2):保留最近 N (默认 64) 个归一化样本重算分位 —— 与
``task_duration_stat`` 的轻量风格一致。徽章规则见 :func:`perf_state`。
版本回归 D17 (§5.3):同因子新版本精确基线建立后 (n≥5),与上一版本对比,
``p50`` 归一化资源增幅 > 阈值 → 发 ``resource.regression.d17`` 结构化告警事件
(经 app_log 接入既有哨兵/SSE 体系)。
"""
from __future__ import annotations

import datetime as dt
import logging

from quant_data import logging as qd_logging
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db.models import RunResourceStat

_log = logging.getLogger("factor.resource_baseline")
_qlog = qd_logging.get_logger("factor.resource_baseline")


# ── signatures (§5.1) ────────────────────────────────────────────────────────
def window_bucket(run_config: dict) -> int:
    """Window length in whole years (rounded) — keeps同口径 runs comparable."""
    start = run_config.get("start")
    end = run_config.get("end")
    try:
        d0 = dt.date.fromisoformat(str(start)[:10])
        d1 = dt.date.fromisoformat(str(end)[:10])
        years = (d1 - d0).days / 365.0
        return max(1, round(years))
    except (ValueError, TypeError):
        return 1


def exact_signature(factor_id: str, version: str, run_config: dict) -> str:
    uni = run_config.get("universe_id", "?")
    freq = run_config.get("rebalance", "?")
    wb = window_bucket(run_config)
    return f"{factor_id}@{version}|{uni}|{freq}|y{wb}"


def class_signature(task_type: str, run_config: dict | None = None) -> str:
    """同类基线 — 镜像 dispatcher 的准入预算类 (方案 §5.1 同类视角)."""
    freq = (run_config or {}).get("rebalance", "")
    if task_type in ("evaluation", "basket_eval"):
        return "factor.eval.minute" if freq in ("min", "minute", "1min") else "factor.eval.daily"
    if task_type in ("correlation", "basket_corr"):
        return "factor.correlation"
    return f"factor.{task_type or 'task'}"


def attribution_kind(attribution: str) -> str:
    """Collapse ``exact`` / ``shared-N`` to the baseline bucket (§5.1)."""
    return "exact" if attribution == "exact" else "shared"


# ── workload normalization (§1 增强 2) ───────────────────────────────────────
def estimate_workload_mrows(
    run_config: dict, *, n_symbols: int | None = None, n_days: int | None = None
) -> float:
    """``symbols × 交易日 / 1e6`` (方案 §1).

    Falls back to a business-day count for the window and a default universe size
    when the caller can't supply exact counts, so归一化 is always defined (cross-
    config comparison only needs a consistent yardstick, not an exact row count).
    """
    if n_days is None:
        try:
            d0 = dt.date.fromisoformat(str(run_config.get("start"))[:10])
            d1 = dt.date.fromisoformat(str(run_config.get("end"))[:10])
            import numpy as np
            n_days = int(np.busday_count(d0, d1)) or (d1 - d0).days
        except (ValueError, TypeError):
            n_days = 252
    n_days = max(1, int(n_days or 1))
    n_symbols = max(1, int(n_symbols or 300))
    return max(0.001, n_symbols * n_days / 1_000_000.0)


# ── percentiles (§5.2) ───────────────────────────────────────────────────────
def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    pos = q * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return float(xs[lo] * (1 - frac) + xs[hi] * frac)


def read_stat(db: Session, signature: str, kind: str) -> RunResourceStat | None:
    return db.get(RunResourceStat, _stat_pk(signature, kind))


def _stat_pk(signature: str, kind: str) -> str:
    return f"{signature}#{kind}"


def update_stat(
    db: Session,
    *,
    signature: str,
    kind: str,
    peak_per_mrow: float,
    cpu_per_mrow: float,
    peak_mb: float,
    cpu_sec: float,
    factor_id: str | None = None,
    version: str | None = None,
    window: int = 64,
) -> RunResourceStat:
    """Append a sample to the signature's rolling window and recompute分位."""
    pk = _stat_pk(signature, kind)
    st = db.get(RunResourceStat, pk)
    if st is None:
        st = RunResourceStat(
            signature=pk, attribution_kind=kind, factor_id=factor_id, version=version,
            recent_peak_per_mrow=[], recent_cpu_per_mrow=[],
            recent_peak_mb=[], recent_cpu_sec=[],
        )
        db.add(st)

    def _push(lst: list, v: float) -> list:
        out = list(lst or [])
        out.append(round(float(v), 4))
        return out[-window:]

    st.recent_peak_per_mrow = _push(st.recent_peak_per_mrow, peak_per_mrow)
    st.recent_cpu_per_mrow = _push(st.recent_cpu_per_mrow, cpu_per_mrow)
    st.recent_peak_mb = _push(st.recent_peak_mb, peak_mb)
    st.recent_cpu_sec = _push(st.recent_cpu_sec, cpu_sec)
    st.factor_id = factor_id or st.factor_id
    st.version = version or st.version

    st.p50_peak_per_mrow = _percentile(st.recent_peak_per_mrow, 0.5)
    st.p90_peak_per_mrow = _percentile(st.recent_peak_per_mrow, 0.9)
    st.p50_cpu_sec_per_mrow = _percentile(st.recent_cpu_per_mrow, 0.5)
    st.p90_cpu_sec_per_mrow = _percentile(st.recent_cpu_per_mrow, 0.9)
    st.p50_peak_mb = _percentile(st.recent_peak_mb, 0.5)
    st.p90_peak_mb = _percentile(st.recent_peak_mb, 0.9)
    st.p50_cpu_sec = _percentile(st.recent_cpu_sec, 0.5)
    st.p90_cpu_sec = _percentile(st.recent_cpu_sec, 0.9)
    st.sample_n = len(st.recent_peak_per_mrow)
    st.updated_at = dt.datetime.utcnow()
    return st


# ── perf badge (§5.2) ────────────────────────────────────────────────────────
def perf_state(
    peak_per_mrow: float, stat: RunResourceStat | None, min_samples: int = 5
) -> str:
    """One of normal|high|abnormal|low|building (设计交付 §5.2 五态)."""
    if stat is None or stat.sample_n < min_samples:
        return "building"
    p90 = stat.p90_peak_per_mrow or 0.0
    p50 = stat.p50_peak_per_mrow or 0.0
    if p50 and peak_per_mrow < p50 * 0.5:
        return "low"
    if p90 and peak_per_mrow > p90 * 1.75:
        return "abnormal"
    if p90 and peak_per_mrow > p90 * 1.25:
        return "high"
    return "normal"


# ── D17 version-regression sentinel (§5.3) ───────────────────────────────────
def check_regression(
    db: Session,
    *,
    factor_id: str,
    version: str,
    run_config: dict,
    settings: Settings | None = None,
) -> dict | None:
    """Compare version N's exact baseline to N-1; emit D17 on > threshold增幅.

    Best-effort: returns the regression payload (and emits the structured alert)
    when triggered, else None. Never raises — a sentinel must not sink a run.
    """
    s = settings or get_settings()
    try:
        cur_sig = exact_signature(factor_id, version, run_config)
        cur = read_stat(db, cur_sig, "exact")
        if cur is None or cur.sample_n < s.resource_baseline_min_samples:
            return None
        prev = _prev_version_stat(db, factor_id, version, run_config, s)
        if prev is None or prev.sample_n < s.resource_baseline_min_samples:
            return None
        thr = s.resource_regression_threshold
        regressions = []
        for label, cur_v, prev_v in (
            ("peak_per_mrow", cur.p50_peak_per_mrow, prev.p50_peak_per_mrow),
            ("cpu_sec_per_mrow", cur.p50_cpu_sec_per_mrow, prev.p50_cpu_sec_per_mrow),
        ):
            if prev_v and cur_v > prev_v * (1.0 + thr):
                regressions.append({
                    "metric": label,
                    "prev": round(prev_v, 2), "current": round(cur_v, 2),
                    "increase_pct": round((cur_v / prev_v - 1.0) * 100, 1),
                })
        if not regressions:
            return None
        payload = {
            "code": "D17", "factor_id": factor_id,
            "version": version, "prev_version": prev.version,
            "regressions": regressions, "threshold_pct": round(thr * 100, 0),
        }
        _qlog.warn("resource.regression.d17", **{
            "factor_id": factor_id, "version": version,
            "prev_version": prev.version,
            "metrics": ",".join(r["metric"] for r in regressions),
            "max_increase_pct": max(r["increase_pct"] for r in regressions),
        })
        _log.warning("D17 resource regression %s@%s vs @%s: %s",
                     factor_id, version, prev.version, regressions)
        return payload
    except Exception:  # noqa: BLE001 - sentinel must never crash the run
        _log.debug("D17 regression check failed", exc_info=True)
        return None


def _prev_version_stat(
    db: Session, factor_id: str, version: str, run_config: dict, s: Settings
) -> RunResourceStat | None:
    """The most-recent OTHER version's exact stat with the same universe/freq/window."""
    uni = run_config.get("universe_id", "?")
    freq = run_config.get("rebalance", "?")
    wb = window_bucket(run_config)
    suffix = f"|{uni}|{freq}|y{wb}#exact"
    rows = (
        db.query(RunResourceStat)
        .filter(
            RunResourceStat.factor_id == factor_id,
            RunResourceStat.attribution_kind == "exact",
            RunResourceStat.version != version,
            RunResourceStat.signature.like(f"%{suffix}"),
        )
        .order_by(RunResourceStat.updated_at.desc())
        .all()
    )
    return rows[0] if rows else None
