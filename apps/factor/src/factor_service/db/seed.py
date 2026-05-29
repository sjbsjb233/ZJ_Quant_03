"""Seed builtins + preset universes on first launch.

The factor service treats every builtin in quant_factor.builtin as a "factor",
inserting a row into the factor + factor_version tables. Preset universes
(全市场 / 沪深 300 / 中证 500 / 中证 800 / 中证 1000 / 上证 50) come from
quant_factor.universe.PRESET_UNIVERSES.

Idempotent — safe to call on every startup.
"""
from __future__ import annotations

import inspect
import logging

from sqlalchemy.orm import Session

from quant_factor.registry import discover_builtins
from quant_factor.universe import PRESET_UNIVERSES

from .models import FactorRecord, FactorVersion, Universe


_log = logging.getLogger(__name__)


def seed_universes(db: Session) -> int:
    """Insert preset universes; skip those already present."""
    inserted = 0
    for u in PRESET_UNIVERSES:
        if db.get(Universe, u["universe_id"]):
            continue
        row = Universe(
            universe_id=u["universe_id"],
            name=u["name"],
            kind=u["kind"],
            preset_flag=u.get("preset_flag")
            if not isinstance(u.get("preset_flag"), tuple)
            else "|".join(u["preset_flag"]),
            classification_system=u.get("classification_system", "SW"),
            default_industry_level=int(u.get("default_industry_level", 1)),
            snapshot_retention_days=int(u.get("snapshot_retention_days", 730)),
            exclude_st=bool(u.get("exclude_st", True)),
            exclude_suspended=bool(u.get("exclude_suspended", True)),
            min_list_days=int(u.get("min_list_days", 0)),
            owner="system",
        )
        db.add(row)
        inserted += 1
    db.commit()
    return inserted


def seed_builtins(db: Session) -> int:
    """Insert one factor + one version per discovered builtin class."""
    inserted = 0
    builtins = discover_builtins()
    for base_name, cls in builtins.items():
        factor_id = base_name  # builtins use the base name as the canonical id
        existing = db.get(FactorRecord, factor_id)
        if existing:
            continue
        params_schema = {
            k: {"type": t.__name__, "default": d}
            for k, (t, d) in (cls.param_schema or {}).items()
        }
        # Honor instance metadata() if implementable for class-level defaults
        cat = getattr(cls, "category", "uncategorized")
        direction = int(getattr(cls, "direction", 1))
        deps = list(getattr(cls, "dependencies", ()))
        tags = list(getattr(cls, "tags", ()))
        display = getattr(cls, "display_name", "") or base_name
        descr = getattr(cls, "description", "") or ""

        rec = FactorRecord(
            factor_id=factor_id,
            base_name=base_name,
            display_name=display,
            category=cat,
            direction=direction,
            source="builtin",
            status="enabled",
            tracking_enabled=False,  # user opts in per design answer
            tags=tags,
            description=descr,
            owner="system",
        )
        db.add(rec)
        ver = FactorVersion(
            factor_id=factor_id,
            version="v1",
            class_name=cls.__name__,
            code_sha256=_hash_source(cls),
            requires_pit=bool(getattr(cls, "requires_pit", False)),
            dependencies=deps,
            param_schema=params_schema,
            enabled=True,
        )
        db.add(ver)
        inserted += 1
    db.commit()
    return inserted


def _hash_source(cls) -> str:
    """Hash the source file of a builtin class for code_sha256."""
    import hashlib

    try:
        src = inspect.getsource(cls)
    except (OSError, TypeError):
        src = cls.__name__
    return hashlib.sha256(src.encode()).hexdigest()


def seed_all(db: Session) -> dict[str, int]:
    return {
        "universes": seed_universes(db),
        "builtins": seed_builtins(db),
    }
