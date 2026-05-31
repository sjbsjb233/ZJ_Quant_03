"""Import endpoints — upload / scan / validate / list pending."""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
from typing import Any

from fastapi import APIRouter, Body, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from quant_data import DataReader
from quant_data import logging as qd_logging
from quant_factor.validate import validate_factor

from ..config import get_settings
from ..db.models import FactorRecord, FactorVersion
from ..deps import current_user, get_db, get_reader, require_editor
from ..errors import ApiError


router = APIRouter()

_qlog = qd_logging.get_logger("factor.imports")


def _safe_id(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_]", "_", s.lower()).strip("_")
    return s or "factor"


def _next_version(db: Session, factor_id: str) -> str:
    """v1 / v2 / v3 ... incremental."""
    existing = (
        db.query(FactorVersion.version)
        .filter(FactorVersion.factor_id == factor_id)
        .all()
    )
    nums = [
        int(v[0][1:])
        for v in existing
        if v[0].startswith("v") and v[0][1:].isdigit()
    ]
    n = max(nums, default=0) + 1
    return f"v{n}"


def _store_source(factor_id: str, version: str, source: str) -> str:
    s = get_settings()
    dest_dir = os.path.join(s.library_dir, factor_id, version)
    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, "factor.py")
    with open(path, "w", encoding="utf-8") as f:
        f.write(source)
    return path


# ─────────────────────────────────────────────────────────────────────────────
@router.post("/imports/validate")
def validate(
    body: dict[str, Any],
    reader: DataReader = Depends(get_reader),
    user=Depends(require_editor),
) -> dict:
    """Dry-run validation — does not register the factor."""
    src = body.get("source") or ""
    if not src.strip():
        raise ApiError("INVALID_PARAMETER", "缺少 source (因子代码)")
    class_hint = body.get("class_name")
    deps = body.get("dependencies") or []
    return validate_factor(
        src, reader=reader, class_name_hint=class_hint, declared_dependencies=deps
    )


# ─────────────────────────────────────────────────────────────────────────────
@router.post("/imports/upload")
async def upload(
    factor_py: UploadFile = File(...),
    readme_md: UploadFile | None = File(None),
    factor_id: str | None = Form(None),
    db: Session = Depends(get_db),
    reader: DataReader = Depends(get_reader),
    user=Depends(require_editor),
) -> dict:
    """Web upload — validate + register a new (disabled) version."""
    py_bytes = await factor_py.read()
    src = py_bytes.decode("utf-8", errors="replace")
    _qlog.info("import.received", filename=factor_py.filename, n_bytes=len(py_bytes))
    rep = validate_factor(src, reader=reader)
    _sm = rep.get("summary") or {}
    if not rep.get("ok"):
        _qlog.warn("import.rejected", filename=factor_py.filename,
                   class_name=rep.get("class_name"), n_bad=int(_sm.get("fail", 0)))
        raise ApiError(
            "PRECONDITION_FAILED",
            "校验失败,无法导入",
            {"report": rep},
        )

    if factor_id is None:
        factor_id = _safe_id(rep.get("class_name") or factor_py.filename or "factor")

    # Create / update FactorRecord (factor_id is the canonical id)
    rec = db.get(FactorRecord, factor_id)
    if rec is None:
        rec = FactorRecord(
            factor_id=factor_id,
            base_name=_safe_id(rep.get("class_name") or factor_id),
            display_name=factor_id,
            category="uncategorized",
            direction=1,
            source="upload",
            status="enabled",
            owner=user.username,
            tags=[],
        )
        db.add(rec)
        db.commit()
        _qlog.info("factor.create", factor_id=factor_id, via="upload",
                   class_name=rep.get("class_name"))

    version = _next_version(db, factor_id)
    code_path = _store_source(factor_id, version, src)
    if readme_md is not None:
        rmd = await readme_md.read()
        with open(
            os.path.join(os.path.dirname(code_path), "README.md"), "wb"
        ) as f:
            f.write(rmd)

    ver = FactorVersion(
        factor_id=factor_id,
        version=version,
        class_name=rep.get("class_name") or "",
        code_sha256=rep.get("code_sha256") or hashlib.sha256(src.encode()).hexdigest(),
        code_path=code_path,
        requires_pit=bool(rep.get("requires_pit")),
        dependencies=list(rep.get("dependencies") or []),
        param_schema=dict(rep.get("param_schema") or {}),
        enabled=False,  # design §8 / §15.7 — manual enable required
        validation_report=rep,
    )
    db.add(ver)
    db.commit()
    _qlog.info("import.parsed", filename=factor_py.filename, factor_id=factor_id,
               version=version, class_name=rep.get("class_name"),
               n_warn=int(_sm.get("warn", 0)))
    return {
        "factor_id": factor_id,
        "version": version,
        "enabled": False,
        "validation_report": rep,
    }


# ─────────────────────────────────────────────────────────────────────────────
@router.post("/imports/scan")
def scan(
    body: dict[str, Any] = Body(default_factory=dict),
    db: Session = Depends(get_db),
    reader: DataReader = Depends(get_reader),
    user=Depends(require_editor),
) -> dict:
    """Walk factor-store/inbox/<name>/factor.py + manifest.yaml; report what
    was found. Files that pass validation are auto-registered with the inbox
    source (enabled=True per design §8 — inbox is trusted)."""
    s = get_settings()
    inbox = body.get("path") or s.inbox_dir
    if not os.path.isdir(inbox):
        return {"items": [], "registered": 0, "path": inbox}

    items: list[dict[str, Any]] = []
    registered = 0
    for entry in sorted(os.listdir(inbox)):
        sub = os.path.join(inbox, entry)
        py = os.path.join(sub, "factor.py")
        if not os.path.isfile(py):
            continue
        with open(py, encoding="utf-8") as f:
            src = f.read()
        rel = os.path.relpath(py, inbox)
        _qlog.info("import.received", filename=rel, via="inbox")
        rep = validate_factor(src, reader=reader)
        _sm = rep.get("summary") or {}
        items.append(
            {
                "file": rel,
                "class_name": rep.get("class_name"),
                "ok": rep.get("ok"),
                "summary": rep.get("summary"),
                "dependencies": rep.get("dependencies"),
            }
        )
        if rep.get("ok"):
            factor_id = _safe_id(entry)
            rec = db.get(FactorRecord, factor_id)
            if rec is None:
                rec = FactorRecord(
                    factor_id=factor_id,
                    base_name=_safe_id(rep.get("class_name") or factor_id),
                    display_name=factor_id,
                    source="inbox",
                    status="enabled",
                    owner=user.username,
                )
                db.add(rec)
                db.commit()
                _qlog.info("factor.create", factor_id=factor_id, via="inbox",
                           class_name=rep.get("class_name"))
            version = _next_version(db, factor_id)
            code_path = _store_source(factor_id, version, src)
            ver = FactorVersion(
                factor_id=factor_id,
                version=version,
                class_name=rep.get("class_name") or "",
                code_sha256=rep.get("code_sha256"),
                code_path=code_path,
                requires_pit=bool(rep.get("requires_pit")),
                dependencies=list(rep.get("dependencies") or []),
                param_schema=dict(rep.get("param_schema") or {}),
                enabled=True,  # inbox trusted
                validation_report=rep,
            )
            db.add(ver)
            db.commit()
            registered += 1
            _qlog.info("import.parsed", filename=rel, factor_id=factor_id,
                       version=version, class_name=rep.get("class_name"),
                       n_warn=int(_sm.get("warn", 0)))
        else:
            _qlog.warn("import.rejected", filename=rel,
                       class_name=rep.get("class_name"), n_bad=int(_sm.get("fail", 0)))
    _qlog.info("import.scan.done", path=inbox, n_scanned=len(items), registered=registered)
    return {"items": items, "registered": registered, "path": inbox}


# ─────────────────────────────────────────────────────────────────────────────
@router.get("/imports/pending")
def list_pending(
    db: Session = Depends(get_db), user=Depends(current_user)
) -> dict:
    """All factor versions still disabled (waiting for review)."""
    rows = (
        db.query(FactorVersion, FactorRecord)
        .join(FactorRecord, FactorVersion.factor_id == FactorRecord.factor_id)
        .filter(FactorVersion.enabled == False)  # noqa: E712
        .order_by(FactorVersion.created_at.desc())
        .all()
    )
    return {
        "items": [
            {
                "factor_id": v.factor_id,
                "factor_display": rec.display_name,
                "version": v.version,
                "class_name": v.class_name,
                "source": rec.source,
                "owner": rec.owner,
                "uploaded_at": v.created_at.isoformat() + "Z"
                if v.created_at
                else None,
                "dependencies": list(v.dependencies or []),
                "requires_pit": v.requires_pit,
                "validation_report": dict(v.validation_report or {}),
                "param_schema": dict(v.param_schema or {}),
                "code_path": v.code_path,
            }
            for v, rec in rows
        ]
    }


@router.get("/imports/code")
def get_code(
    factor_id: str,
    version: str,
    db: Session = Depends(get_db),
    user=Depends(current_user),
) -> dict:
    v = (
        db.query(FactorVersion)
        .filter(
            FactorVersion.factor_id == factor_id, FactorVersion.version == version
        )
        .first()
    )
    if v is None:
        raise ApiError("VERSION_NOT_FOUND", f"版本 {factor_id}@{version} 不存在")
    if v.code_path and os.path.exists(v.code_path):
        with open(v.code_path, encoding="utf-8") as f:
            return {
                "factor_id": factor_id,
                "version": version,
                "class_name": v.class_name,
                "source": f.read(),
            }
    # Builtin — pull the source from quant_factor.builtin via inspect
    import inspect

    from quant_factor.registry import all_registered

    base_name = (
        db.get(FactorRecord, factor_id).base_name if db.get(FactorRecord, factor_id) else None
    )
    cls = all_registered().get(base_name) if base_name else None
    if cls is None:
        return {
            "factor_id": factor_id,
            "version": version,
            "class_name": v.class_name,
            "source": "# builtin source unavailable",
        }
    try:
        src = inspect.getsource(inspect.getmodule(cls))
    except Exception:  # noqa: BLE001
        src = "# (could not read builtin module source)"
    return {
        "factor_id": factor_id,
        "version": version,
        "class_name": v.class_name,
        "source": src,
    }
