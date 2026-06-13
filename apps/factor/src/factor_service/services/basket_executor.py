"""Basket sub-task executors — basket_eval (increment-aware) + basket_corr.

Reuses the existing run system (FactorRun → preflight / degradation台账 / logs)
so the 红线 (industry/size missing ⇒ never silently success) is inherited
verbatim. Writes results to the basket ledger instead of the per-factor dir.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import time
import uuid
from typing import Any

from quant_data import DataReader
from quant_data import logging as qd_logging
from quant_factor import EvalConfig, FactorEvaluator
from quant_factor.base import RunCancelled
from quant_factor.cache import data_fingerprint
from quant_factor.degradation import DegradationLedger
from quant_factor.preflight import PreflightBlocked, check_result_to_json, run_preflight
from quant_factor.registry import instantiate, instantiate_from_code

from ..config import get_settings
from ..db.models import (
    Basket,
    BasketItemState,
    BasketRun,
    BasketRunItem,
    FactorRecord,
    FactorRun,
    FactorRunDegradation,
    FactorVersion,
    Task,
    Universe,
    UniverseMember,
)
from ..baskets import keys, ledger
from ..baskets.correlation import compute_ic_correlation
from ..baskets.snapshots import build_snapshot
from ..queue import store
from . import resource_writer
from .resource_collector import ResourceCollector
from .results_writer import write_degradations

_log = logging.getLogger("factor.basket_executor")
_qlog = qd_logging.get_logger("factor.basket_executor")


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


def _uni_dict(u: Universe) -> dict:
    return {
        "universe_id": u.universe_id, "kind": u.kind, "preset_flag": u.preset_flag,
        "classification_system": u.classification_system,
        "default_industry_level": u.default_industry_level,
        "exclude_st": u.exclude_st, "exclude_suspended": u.exclude_suspended,
        "min_list_days": u.min_list_days,
        "snapshot_retention_days": u.snapshot_retention_days,
    }


def _resolve_version(db, factor_id: str, version: str | None) -> FactorVersion | None:
    if version:
        return (
            db.query(FactorVersion)
            .filter(FactorVersion.factor_id == factor_id, FactorVersion.version == version)
            .first()
        )
    return (
        db.query(FactorVersion)
        .filter(FactorVersion.factor_id == factor_id, FactorVersion.enabled == True)  # noqa: E712
        .order_by(FactorVersion.id.desc())
        .first()
    )


def _load_factor(rec: FactorRecord, ver: FactorVersion, params: dict):
    if ver.code_path and os.path.exists(ver.code_path):
        with open(ver.code_path, encoding="utf-8") as fh:
            return instantiate_from_code(fh.read(), ver.class_name, params)
    return instantiate(rec.base_name, params)


# ═════════════════════════════════════════════════════════════════════════════
def run_basket_eval(db, t: Task) -> dict[str, Any]:
    p = t.payload or {}
    item_key = p["item_key"]
    basket_id = p["basket_id"]
    revision = int(p["revision"])
    factor_id = p["factor_id"]
    params = p.get("params") or {}
    shared = p.get("shared_config") or {}
    rstart = dt.date.fromisoformat(p["resolved_start"]) if p.get("resolved_start") else None
    rend = dt.date.fromisoformat(p["resolved_end"]) if p.get("resolved_end") else None
    settings = get_settings()

    bri = (
        db.query(BasketRunItem)
        .filter(BasketRunItem.brun_id == p["brun_id"], BasketRunItem.item_id == p["item_id"])
        .first()
    )

    rec = db.get(FactorRecord, factor_id)
    ver = _resolve_version(db, factor_id, p.get("version"))
    if rec is None or ver is None:
        if bri:
            bri.status = "failed"
            db.commit()
        raise ValueError(f"factor/{factor_id} version not resolvable")

    run_id = f"frun_{uuid.uuid4().hex[:16]}"
    cfg_dict = keys.eval_config_dict(shared, rstart, rend)
    run = FactorRun(
        run_id=run_id, factor_id=factor_id, version=ver.version, params=params,
        run_config=cfg_dict, status="running", started_at=_now(),
        task_id=t.task_id, trace_id=t.trace_id, initiated_by=t.initiated_by,
    )
    db.add(run)
    if bri:
        bri.run_id = run_id
        bri.inc_from = rstart
        bri.inc_to = rend
    db.commit()

    _qlog.info("brun.child.start", brun_id=p["brun_id"], item_key=item_key,
               factor_id=factor_id, run_id=run_id)
    # 资源画像 (方案 §4): basket children also accrue a per-run summary so the篮子
    # 成本排行表 can rank by精确 CPU + attribute peak内存. Best-effort, fail-safe.
    collector = (
        ResourceCollector(task_id=t.task_id, run_id=run_id,
                          factor_id=factor_id, version=ver.version)
        if settings.resource_sampling_enabled else None
    )
    _ns = [None]  # final symbol count for workload归一化 (filled after universe resolve)
    try:
        rc = collector.begin("load") if collector else None
        cfg = EvalConfig.from_dict(cfg_dict)
        factor = _load_factor(rec, ver, params)
        uni_row = db.get(Universe, cfg.universe_id)
        if uni_row is None:
            raise ValueError(f"universe {cfg.universe_id} not found")
        uni_def = _uni_dict(uni_row)
        custom = None
        if uni_row.kind == "custom":
            custom = [r[0] for r in db.query(UniverseMember.symbol)
                      .filter(UniverseMember.universe_id == uni_row.universe_id).all()]
        _ns[0] = len(custom) if custom else None

        reader = DataReader(root=settings.quant_data_root)
        try:
            fp = data_fingerprint(reader, cfg)
        except Exception:  # noqa: BLE001
            fp = None
        if collector:
            collector.end(rc)

        # ── preflight gate (红线 inherited) ──────────────────────────────────
        dledger = None
        rc = collector.begin("preflight") if collector else None
        if settings.preflight_enabled:
            try:
                from quant_factor.universe import resolve_universe
                uni = resolve_universe(reader, uni_def, custom, cfg.start, cfg.end)
                symbols = sorted(uni["symbol"].unique().to_list()) if uni.height else list(custom or [])
                if symbols:
                    _ns[0] = len(symbols)
            except Exception:  # noqa: BLE001
                symbols = list(custom or [])
            policy_overrides = (shared or {}).get("degradation_policy")
            try:
                _blocked, dledger = run_preflight(
                    reader, cfg, uni_def, symbols, cfg.start, cfg.end,
                    profile=settings.degradation_policy_profile,
                    policy_overrides=policy_overrides,
                )
            except PreflightBlocked as pb:
                if collector:
                    collector.end(rc)
                return _finish_blocked(db, run, bri, t, pb)
        if dledger is None:
            dledger = DegradationLedger()
        if collector:
            collector.end(rc)

        def progress(c, n):
            store.heartbeat(db, t.task_id, current=int(c), total=int(n))

        def cancelled():
            db.refresh(t)
            return bool(t.cancel_requested)

        rc = collector.begin("compute") if collector else None
        evaluator = FactorEvaluator(reader, ledger=dledger, progress_cb=progress,
                                    cancel_cb=cancelled)
        result = evaluator.evaluate(factor, cfg, uni_def, custom)
        if collector:
            collector.end(rc)

        # ── write the item ledger + derive settle/pending ────────────────────
        rc = collector.begin("write") if collector else None
        idir = ledger.item_dir(settings.factor_store_root, basket_id, revision, item_key)
        counts = ledger.write_ledger(idir, result, int(cfg.primary_horizon))
        settled, pending, last_rebal = ledger.settled_split(result, int(cfg.primary_horizon))

        st = db.get(BasketItemState, item_key)
        if st is None:
            st = BasketItemState(item_key=item_key, basket_id=basket_id)
            db.add(st)
        st.revision = revision
        st.item_id = p["item_id"]
        st.factor_id = factor_id
        st.last_rebalance_date = last_rebal
        st.last_settled_date = settled[-1] if settled else None
        st.pending_dates = [d.isoformat() for d in pending]
        st.data_fingerprint = fp
        st.artifact_dir = idir
        st.rows_factor = counts["rows_factor"]
        st.rows_ic = len(settled)

        final = "degraded" if dledger.count > 0 else "success"
        run.status = final
        run.degradation_level = dledger.level()
        run.degradation_count = dledger.count
        run.has_critical = dledger.has_critical
        run.summary = dict(result.get("summary") or {})
        run.finished_at = _now()
        run.duration_sec = (run.finished_at - run.started_at).total_seconds()
        _persist_degradation(db, run_id, dledger)
        if bri:
            bri.status = final
        db.commit()
        if collector:
            collector.end(rc)
        # Write the degradations artifact too (not just DB rows) so the dedicated
        # /runs/{id}/degradations endpoint + run-detail drawer show basket-child
        # degradations — the live stack revealed this was DB-only before.
        try:
            write_degradations(run_id, status=final, level=dledger.level(),
                               events=dledger.to_jsonable())
        except Exception:  # noqa: BLE001 — artifact is best-effort
            pass
        _qlog.info("brun.child.done", brun_id=p["brun_id"], item_key=item_key,
                   status=final, settled=len(settled), pending=len(pending))
        return {"task_id": t.task_id, "status": final, "summary": run.summary}

    except RunCancelled:
        run.status = "canceled"
        run.finished_at = _now()
        if bri:
            bri.status = "canceled"
        db.commit()
        return {"task_id": t.task_id, "status": "canceled"}
    except Exception as e:  # noqa: BLE001
        run.status = "failed"
        run.error = f"{type(e).__name__}: {e}"
        run.finished_at = _now()
        if bri:
            bri.status = "failed"
        db.commit()
        _qlog.error("brun.child.failed", exc=e, brun_id=p["brun_id"], item_key=item_key)
        raise
    finally:
        _finalize_child_resources(db, collector, run, cfg_dict, _ns[0], settings)


def _finalize_child_resources(db, collector, run, run_config, n_symbols, settings) -> None:
    """资源画像收尾 for a basket child (方案 §4) — summary/baseline + trace节. Fail-safe."""
    if collector is None:
        return
    try:
        from .resource_sampler import REGISTRY
        resources = resource_writer.finalize_run_resources(
            db, collector, run_config=run_config, task_type="basket_eval",
            profiled=False, n_symbols=n_symbols,
            admission=REGISTRY.get_admission(collector.task_id), settings=settings,
        )
        if resources is not None:
            resource_writer.merge_resources_into_trace(run.run_id, resources)
    except Exception:  # noqa: BLE001 - 收尾 best-effort, 绝不影响 run 终态
        _log.warning("basket child resource finalize failed (non-fatal)", exc_info=True)


def _finish_blocked(db, run, bri, t, pb) -> dict[str, Any]:
    blocked_json = [check_result_to_json(r) for r in pb.results]
    has_critical = any(b.get("severity") == "critical" for b in blocked_json)
    run.status = "failed"
    run.error = "preflight_blocked"
    run.preflight = blocked_json
    run.degradation_level = "severe" if has_critical else "partial"
    run.degradation_count = len(blocked_json)
    run.has_critical = has_critical
    run.finished_at = _now()
    if run.started_at:
        run.duration_sec = (run.finished_at - run.started_at).total_seconds()
    for r in pb.results:
        ev = r.event
        if ev is None:
            continue
        db.add(FactorRunDegradation(
            run_id=run.run_id, code=ev.code, category=ev.category, severity=ev.severity,
            stage=ev.stage, config_key=ev.config_key, title=ev.title, reason=ev.reason,
            impact=ev.impact, suggested_action=ev.suggested_action, policy="block",
            evidence_json=ev.evidence, occurred_at=ev.occurred_at,
        ))
    if bri:
        bri.status = "failed"
    db.commit()
    try:
        write_degradations(run.run_id, status="failed", level=run.degradation_level,
                           blocked=blocked_json, events=[])
    except Exception:  # noqa: BLE001
        pass
    return {"task_id": t.task_id, "status": "failed", "reason": "preflight_blocked",
            "blocked": [r.check_id for r in pb.results]}


def _persist_degradation(db, run_id: str, dledger) -> None:
    for ev in dledger.events:
        db.add(FactorRunDegradation(
            run_id=run_id, code=ev.code, category=ev.category, severity=ev.severity,
            stage=ev.stage, config_key=ev.config_key, title=ev.title, reason=ev.reason,
            impact=ev.impact, suggested_action=ev.suggested_action, policy=ev.policy,
            evidence_json=ev.evidence, occurred_at=ev.occurred_at,
        ))


# ═════════════════════════════════════════════════════════════════════════════
def run_basket_corr(db, t: Task) -> dict[str, Any]:
    p = t.payload or {}
    basket_id = p["basket_id"]
    revision = int(p["revision"])
    items = p.get("items") or []
    settings = get_settings()
    run = db.get(BasketRun, p["brun_id"])
    if run is None:
        return {"task_id": t.task_id, "status": "failed", "reason": "brun_missing"}

    # Read each item's settled IC series; exclude failed/severe items implicitly
    # (no ledger → no series). build_snapshot records the excluded names.
    series_by_item: dict[str, dict[str, float]] = {}
    labels: dict[str, str] = {}
    for it in items:
        ik = it["item_key"]
        idir = ledger.item_dir(settings.factor_store_root, basket_id, revision, ik)
        ser = ledger.read_ic_for_corr(idir)
        if ser:
            series_by_item[ik] = ser
            labels[ik] = it.get("label") or ik

    basket = db.get(Basket, basket_id)
    corr_json = compute_ic_correlation(
        series_by_item, labels,
        method=p.get("method") or "pearson",
        threshold=float(p.get("threshold") or 0.7),
        min_periods=int(p.get("min_periods") or (basket.corr_min_periods if basket else 12)),
    )
    snap_id = build_snapshot(db, run, corr_json=corr_json)
    run.snapshot_id = snap_id
    db.commit()
    _qlog.info("brun.corr.done", brun_id=p["brun_id"], n=len(series_by_item),
               pairs=len(corr_json.get("high_pairs") or []), snapshot_id=snap_id)
    return {"task_id": t.task_id, "status": "success",
            "summary": {"n_items": len(series_by_item),
                        "high_pairs": len(corr_json.get("high_pairs") or [])}}
