"""Dry-run preflight (设计 §10.2) — shared by the ``POST /runs/preflight``
endpoint and the ``POST /runs`` submission guard.

Resolves factor → enabled version → universe def → window symbol set, then runs
the same :func:`quant_factor.evaluate_checks` the worker uses, and classifies the
non-pass results into ``would_block`` / ``would_degrade``. It NEVER computes and
NEVER writes — purely advisory so the UI can warn (or the API can 422) before a
task is ever queued.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.orm import Session

from quant_data import DataReader
from quant_factor.config import EvalConfig
from quant_factor.preflight import check_result_to_json, evaluate_checks

from ..config import get_settings
from ..db.models import FactorRecord, FactorVersion, Universe


def _universe_row_to_dict(u: Universe) -> dict:
    return {
        "universe_id": u.universe_id,
        "kind": u.kind,
        "preset_flag": u.preset_flag,
        "classification_system": u.classification_system,
        "default_industry_level": u.default_industry_level,
        "exclude_st": u.exclude_st,
        "exclude_suspended": u.exclude_suspended,
        "min_list_days": u.min_list_days,
        "snapshot_retention_days": u.snapshot_retention_days,
    }


def dry_run_preflight(
    db: Session,
    *,
    factor_id: str,
    version: str | None,
    run_config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return ``{would_block, would_degrade, runnable, ...}`` (设计 §10.2).

    Raises ValueError on unresolvable factor/version/universe so the caller can
    translate to a clean 4xx.
    """
    settings = get_settings()

    rec = db.get(FactorRecord, factor_id)
    if rec is None:
        raise ValueError(f"因子 {factor_id} 不存在")

    if not version:
        ver = (
            db.query(FactorVersion)
            .filter(FactorVersion.factor_id == factor_id, FactorVersion.enabled == True)  # noqa: E712
            .order_by(FactorVersion.id.desc())
            .first()
        )
    else:
        ver = (
            db.query(FactorVersion)
            .filter(
                FactorVersion.factor_id == factor_id,
                FactorVersion.version == version,
            )
            .first()
        )
    if ver is None:
        raise ValueError(f"因子 {factor_id} 无可用版本")

    cfg = EvalConfig.from_dict(run_config or {})
    if not (cfg.start and cfg.end):
        cfg.end = dt.date.today()
        cfg.start = cfg.end - dt.timedelta(days=365)

    uni_row = db.get(Universe, cfg.universe_id)
    if uni_row is None:
        raise ValueError(f"股票池 {cfg.universe_id} 不存在")
    uni_def = _universe_row_to_dict(uni_row)

    custom_syms = None
    if uni_row.kind == "custom":
        from ..db.models import UniverseMember

        custom_syms = [
            r[0]
            for r in db.query(UniverseMember.symbol)
            .filter(UniverseMember.universe_id == uni_row.universe_id)
            .all()
        ]

    reader = DataReader(root=settings.quant_data_root)
    symbols = _resolve_symbols(reader, uni_def, custom_syms, cfg)

    if not settings.preflight_enabled:
        return {
            "enabled": False,
            "would_block": [],
            "would_degrade": [],
            "runnable": True,
            "profile": settings.degradation_policy_profile,
        }

    policy_overrides = (run_config or {}).get("degradation_policy")
    results, _policy = evaluate_checks(
        reader, cfg, uni_def, symbols, cfg.start, cfg.end,
        profile=settings.degradation_policy_profile,
        policy_overrides=policy_overrides,
    )
    would_block = [
        check_result_to_json(r) for r in results if r.status == "block"
    ]
    would_degrade = [
        check_result_to_json(r) for r in results if r.status == "degrade"
    ]
    return {
        "enabled": True,
        "profile": settings.degradation_policy_profile,
        "n_symbols": len(symbols),
        "would_block": would_block,
        "would_degrade": would_degrade,
        "runnable": len(would_block) == 0,
    }


def _resolve_symbols(reader, uni_def, custom_syms, cfg) -> list[str]:
    from quant_factor.universe import resolve_universe

    try:
        uni = resolve_universe(reader, uni_def, custom_syms, cfg.start, cfg.end)
        if uni.height == 0:
            return list(custom_syms or [])
        return sorted(uni["symbol"].unique().to_list())
    except Exception:  # noqa: BLE001
        return list(custom_syms or [])
