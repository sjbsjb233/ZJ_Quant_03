"""运行资源画像收尾 (资源画像方案 §3.3 / §4).

任务结束时:从环形缓冲切出 ``[task_start, task_end]`` 样本 → max-pooling 降采样至
≤300 点 (峰值绝不允许被平均抹掉) → 组装 trace.json ``resources`` 节;同时计算汇总
写 ``run_resource_summary`` 表、滚动更新两级签名基线、落全分辨率样本工件,并跑 D17。

降采样一律 max-pooling (每桶取峰值),禁止均值 —— 画像的使命是抓尖峰 (§9)。
全程 best-effort:任何异常仅 warn,绝不影响 run 的终态。
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import logging
import os

from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db.models import FactorRecord, RunResourceSummary
from . import resource_baseline as bl
from .resource_collector import ResourceCollector
from .resource_sampler import REGISTRY, slice_samples
from .results_writer import _json_default

_log = logging.getLogger("factor.resource_writer")

# 资源时间线样本通道顺序: [ts_rel, rss_mb, cpu_pct, arrow_mb, in_flight]
_CHANNELS = ("rss_mb", "cpu_pct", "arrow_mb", "in_flight")


# ── max-pool downsample (§3.3 / §9) ──────────────────────────────────────────
def max_pool(samples: list[dict], max_points: int) -> list[list]:
    """Bucket samples by time into ≤max_points; per-channel max within each桶.

    Returns rows ``[ts_rel, rss, cpu, arrow, in_flight]``. Independent channel max
    guarantees no peak is ever averaged away (the whole point of the画像)."""
    if not samples:
        return []
    n = len(samples)
    if n <= max_points:
        return [_row(s) for s in samples]
    t0, t1 = samples[0]["t"], samples[-1]["t"]
    span = (t1 - t0) or 1.0
    buckets: dict[int, dict] = {}
    for s in samples:
        b = min(max_points - 1, int((s["t"] - t0) / span * max_points))
        cur = buckets.get(b)
        if cur is None:
            buckets[b] = {"t": s["t"], **{c: s[c] for c in _CHANNELS}}
        else:
            # keep the bucket's ts at the peak-rss sample so the spike stays真实
            if s["rss_mb"] > cur["rss_mb"]:
                cur["t"] = s["t"]
            for c in _CHANNELS:
                if s[c] > cur[c]:
                    cur[c] = s[c]
    return [_row(buckets[b]) for b in sorted(buckets)]


def _row(s: dict) -> list:
    return [
        round(s["t"], 1), round(s["rss_mb"], 1), round(s["cpu_pct"], 1),
        round(s["arrow_mb"], 1), int(s["in_flight"]),
    ]


# ── full-resolution + profile artifacts (§4.3) ───────────────────────────────
def _gz_path(run_id: str, kind: str, settings: Settings | None = None) -> str:
    s = settings or get_settings()
    return os.path.join(s.runs_dir, f"{run_id}.{kind}.json.gz")


def write_gz_artifact(run_id: str, kind: str, payload: dict) -> str:
    s = get_settings()
    os.makedirs(s.runs_dir, exist_ok=True)
    path = _gz_path(run_id, kind, s)
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(payload, f, default=_json_default, ensure_ascii=False,
                  separators=(",", ":"))
    return path


def read_gz_artifact(run_id: str, kind: str) -> dict | None:
    path = _gz_path(run_id, kind)
    if not os.path.exists(path):
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def read_samples(run_id: str) -> dict | None:
    return read_gz_artifact(run_id, "samples")


def read_ndjson_window(
    start_ts: float, end_ts: float, settings: Settings | None = None
) -> list[dict]:
    """Worker NDJSON samples whose ts ∈ [start, end] (篮子/全局视图切片, 方案 §3.1).

    The API process reads the worker's daily NDJSON from the shared store. Reads
    only the day-files the window spans (incl. ``{day}.N`` rollovers). Best-effort:
    a malformed line or missing file is skipped, never raised.
    """
    import time
    s = settings or get_settings()
    d = s.worker_samples_dir
    if not os.path.isdir(d):
        return []
    days: set[str] = set()
    t = start_ts - 86400
    while t <= end_ts + 86400:
        days.add(time.strftime("%Y%m%d", time.gmtime(t)))
        t += 86400
    out: list[dict] = []
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    for name in names:
        if not name.endswith(".ndjson") or name[:8] not in days:
            continue
        try:
            with open(os.path.join(d, name), encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    ts = rec.get("ts")
                    if ts is not None and start_ts <= ts <= end_ts:
                        out.append(rec)
        except OSError:
            continue
    out.sort(key=lambda r: r.get("ts", 0))
    return out


def read_profile(run_id: str) -> dict | None:
    return read_gz_artifact(run_id, "profile")


def merge_resources_into_trace(
    run_id: str, resources: dict, fallback_payload: dict | None = None
) -> None:
    """Inject the resources节 into the run's trace.json (方案 §4.1).

    Any terminal path already wrote the trace; if采集 is off (or this is a basket
    child with no forensic trace), a minimal artifact is created so the资源画像
    block can render standalone. Best-effort — never raises.
    """
    from .results_writer import read_run_trace, write_run_trace
    try:
        payload = read_run_trace(run_id)
        if payload is None:
            payload = fallback_payload or {
                "run_id": run_id, "schema_version": 1,
                "stages": [], "days": [], "meta": {},
            }
        payload["resources"] = resources
        write_run_trace(run_id, payload)
    except Exception:  # noqa: BLE001
        _log.debug("merge resources into trace failed", exc_info=True)


# ── attribution (§2) ─────────────────────────────────────────────────────────
def _attribution(samples: list[dict], run_id: str) -> tuple[str, int]:
    """``exact`` iff in_flight==1 全程; else ``shared-N`` (N = 同期最大并发)."""
    sampled_max = max((int(s["in_flight"]) for s in samples), default=1)
    reg_max = REGISTRY.peak_concurrency(run_id) if run_id else 1
    n = max(1, sampled_max, reg_max)
    return ("exact" if n <= 1 else f"shared-{n}"), n


# ── the orchestrator ─────────────────────────────────────────────────────────
def finalize_run_resources(
    db: Session,
    collector: ResourceCollector,
    *,
    run_config: dict,
    task_type: str = "evaluation",
    profiled: bool = False,
    n_symbols: int | None = None,
    n_days: int | None = None,
    admission: list[dict] | None = None,
    settings: Settings | None = None,
) -> dict | None:
    """Build the trace ``resources`` node + persist summary/baseline/artifacts.

    Returns the resources dict (to embed in trace.json) or None on failure /
    disabled. Never raises.
    """
    s = settings or get_settings()
    if not s.resource_sampling_enabled:
        return None
    try:
        return _finalize(db, collector, run_config, task_type, profiled,
                         n_symbols, n_days, admission or [], s)
    except Exception:  # noqa: BLE001 - 收尾必须 best-effort, 绝不影响 run 终态
        _log.warning("finalize_run_resources failed (non-fatal)", exc_info=True)
        return None


def _finalize(db, collector, run_config, task_type, profiled,
              n_symbols, n_days, admission, s):
    run_id = collector.run_id
    end_wall = dt.datetime.utcnow().timestamp()
    raw = slice_samples(collector.t0_wall, end_wall)
    # normalise to relative time (seconds from run start)
    t0 = collector.t0_wall
    samples = [
        {"t": max(0.0, sr["ts"] - t0), "rss_mb": sr["rss_mb"], "cpu_pct": sr["cpu_pct"],
         "arrow_mb": sr["arrow_mb"], "in_flight": sr["in_flight"], "tasks": sr.get("tasks", [])}
        for sr in raw
    ]
    samples.sort(key=lambda x: x["t"])

    attribution, conc_max = _attribution(raw, run_id)
    attr_kind = bl.attribution_kind(attribution)

    # ── per-stage records (+ in_flight_max from the sample window) ──────────
    stage_nodes = []
    for st in collector.stages:
        win = [sr for sr in raw if st.from_ts - 0.5 <= sr["ts"] <= st.to_ts + 0.5]
        if_max = max((int(x["in_flight"]) for x in win), default=1)
        st.in_flight_max = if_max
        stage_nodes.append({
            "stage": st.stage,
            "from": round(max(0.0, st.from_ts - t0), 1),
            "to": round(max(0.0, st.to_ts - t0), 1),
            "cpu_sec": round(st.cpu_sec, 2),
            "rss_start_mb": round(st.rss_start_mb, 1),
            "rss_end_mb": round(st.rss_end_mb, 1),
            "arrow_delta_mb": round(st.arrow_delta_mb, 1),
            "in_flight_max": if_max,
        })

    # ── summary metrics ─────────────────────────────────────────────────────
    # peak RSS: prefer the sampled series; fall back to stage end-RSS readings
    # when the run was too short for the 1Hz sampler to catch a point.
    _stage_peak = max((x.rss_end_mb for x in collector.stages), default=0.0)
    peak_rss = max((sr["rss_mb"] for sr in raw), default=_stage_peak)
    arrow_peak = max((sr["arrow_mb"] for sr in raw), default=0.0)
    cpu_sec = sum(st.cpu_sec for st in collector.stages)
    wall = max(0.001, end_wall - t0)
    avg_cpu = (sum(sr["cpu_pct"] for sr in raw) / len(raw)) if raw else (cpu_sec / wall * 100)
    workload = bl.estimate_workload_mrows(run_config, n_symbols=n_symbols, n_days=n_days)
    peak_per_mrow = peak_rss / workload
    cpu_per_mrow = cpu_sec / workload
    threads = int(os.environ.get("POLARS_MAX_THREADS") or os.environ.get("OMP_NUM_THREADS") or 1)

    # ── baselines: roll the two-level signatures, read back for compare ──────
    fid, ver = collector.factor_id, collector.version
    exact_sig = bl.exact_signature(fid, ver, run_config)
    class_sig = bl.class_signature(task_type, run_config)
    bl.update_stat(db, signature=exact_sig, kind=attr_kind,
                   peak_per_mrow=peak_per_mrow, cpu_per_mrow=cpu_per_mrow,
                   peak_mb=peak_rss, cpu_sec=cpu_sec, factor_id=fid, version=ver,
                   window=s.resource_baseline_window)
    bl.update_stat(db, signature=class_sig, kind=attr_kind,
                   peak_per_mrow=peak_per_mrow, cpu_per_mrow=cpu_per_mrow,
                   peak_mb=peak_rss, cpu_sec=cpu_sec, window=s.resource_baseline_window)
    db.commit()

    exact_stat = bl.read_stat(db, exact_sig, attr_kind)
    class_stat = bl.read_stat(db, class_sig, attr_kind)
    state = bl.perf_state(peak_per_mrow, exact_stat, s.resource_baseline_min_samples)

    def _p(stat, attr):
        return round(getattr(stat, attr), 1) if stat else None

    summary = {
        "peak_rss_mb": round(peak_rss, 1),
        "cpu_sec": round(cpu_sec, 1),
        "avg_cpu_pct": round(avg_cpu, 0),
        "arrow_peak_mb": round(arrow_peak, 1),
        "workload_mrows": round(workload, 3),
        "peak_per_mrow": round(peak_per_mrow, 1),
        "cpu_sec_per_mrow": round(cpu_per_mrow, 2),
        "threads": threads,
        "perf_state": state,
        "signature": exact_sig,
        "class_signature": class_sig,
        "baseline_sample_n": exact_stat.sample_n if exact_stat else 0,
        # normalized baselines (MB/百万行) — for the对比条
        "baseline_p50_norm": _p(exact_stat, "p50_peak_per_mrow"),
        "baseline_p90_norm": _p(exact_stat, "p90_peak_per_mrow"),
        "class_p50_norm": _p(class_stat, "p50_peak_per_mrow"),
        "class_p90_norm": _p(class_stat, "p90_peak_per_mrow"),
        "cpu_norm_p50": _p(exact_stat, "p50_cpu_sec_per_mrow"),
        "cpu_norm_p90": _p(exact_stat, "p90_cpu_sec_per_mrow"),
        # absolute baselines (MB) = 归一化基线 × 本次 workload (v1.2 §7.1②口径)
        "baseline_p50_abs_mb": round((exact_stat.p50_peak_per_mrow * workload), 0)
        if exact_stat and exact_stat.sample_n else None,
        "baseline_p90_abs_mb": round((exact_stat.p90_peak_per_mrow * workload), 0)
        if exact_stat and exact_stat.sample_n else None,
    }

    # ── concurrency context (shared 归因 — 同期任务列表) ─────────────────────
    conc_tasks = _concurrent_tasks(db, raw, run_id, t0)

    # ── admission events → relative ts ──────────────────────────────────────
    adm_nodes = []
    for ev in admission:
        adm_nodes.append({
            "ts": round(max(0.0, (ev.get("ts", t0)) - t0), 1),
            "event": ev.get("event", "granted"),
            "wait_ms": ev.get("wait_ms"),
        })

    has_fullres = bool(samples) and s.resource_fullres_enabled

    resources = {
        "attribution": attribution,
        "sample_interval_s": s.resource_sample_interval_s,
        "has_fullres": has_fullres,
        "has_profile": bool(profiled),
        "samples": max_pool(samples, s.resource_max_points),
        "stages": stage_nodes,
        "admission": adm_nodes,
        "concurrent_tasks": conc_tasks,
        "summary": summary,
        "sample_n": len(samples),
        "duration_s": round(wall, 1),
    }

    # ── full-resolution artifact (lazy-loaded on first zoom, §3.4) ──────────
    if has_fullres:
        try:
            write_gz_artifact(run_id, "samples", {
                "run_id": run_id, "sample_interval_s": s.resource_sample_interval_s,
                "t0": t0, "attribution": attribution,
                "samples": [_row(x) for x in samples],
            })
        except Exception:  # noqa: BLE001
            resources["has_fullres"] = False
            _log.debug("full-res samples artifact write failed", exc_info=True)

    # ── run_resource_summary row (1/run, 永久) ──────────────────────────────
    _write_summary_row(db, run_id, fid, ver, exact_sig, class_sig, attribution,
                       summary, conc_max, len(samples), profiled, t0, end_wall)

    # ── D17 version-regression sentinel (§5.3) ──────────────────────────────
    bl.check_regression(db, factor_id=fid, version=ver, run_config=run_config,
                        settings=s)
    return resources


def _concurrent_tasks(db, raw, run_id, t0) -> list[dict]:
    spans: dict[str, list[float]] = {}
    for sr in raw:
        for rid in sr.get("tasks", []):
            if rid and rid != run_id:
                spans.setdefault(rid, [sr["ts"], sr["ts"]])
                spans[rid][0] = min(spans[rid][0], sr["ts"])
                spans[rid][1] = max(spans[rid][1], sr["ts"])
    out = []
    for rid, (a, b) in list(spans.items())[:12]:
        name, kind = _run_label(db, rid)
        out.append({
            "run_id": rid, "name": name, "type": kind,
            "from": round(max(0.0, a - t0), 1), "to": round(max(0.0, b - t0), 1),
        })
    return out


def _run_label(db, run_id: str) -> tuple[str, str]:
    try:
        from ..db.models import FactorRun
        r = db.get(FactorRun, run_id)
        if r is None:
            return run_id, "任务"
        rec = db.get(FactorRecord, r.factor_id)
        name = (rec.display_name or rec.base_name) if rec else r.factor_id
        kind = "篮子子任务" if getattr(r, "task_id", None) else "评估"
        return f"{name}@{r.version}", kind
    except Exception:  # noqa: BLE001
        return run_id, "任务"


def _write_summary_row(db, run_id, fid, ver, exact_sig, class_sig, attribution,
                       summary, conc_max, sample_n, profiled, t0, end_wall):
    row = db.get(RunResourceSummary, run_id)
    if row is None:
        row = RunResourceSummary(run_id=run_id)
        db.add(row)
    row.factor_id = fid
    row.version = ver
    row.signature = exact_sig
    row.class_signature = class_sig
    row.attribution = attribution
    row.peak_rss_mb = summary["peak_rss_mb"]
    row.cpu_sec = summary["cpu_sec"]
    row.avg_cpu_pct = summary["avg_cpu_pct"]
    row.arrow_peak_mb = summary["arrow_peak_mb"]
    row.workload_mrows = summary["workload_mrows"]
    row.peak_per_mrow = summary["peak_per_mrow"]
    row.cpu_sec_per_mrow = summary["cpu_sec_per_mrow"]
    row.concurrent_max = conc_max
    row.sample_n = sample_n
    row.profiled = bool(profiled)
    row.started_at = dt.datetime.utcfromtimestamp(t0)
    row.finished_at = dt.datetime.utcfromtimestamp(end_wall)
    db.commit()
