"""Factor code validator — design §8 + §19.2-E (6 named checks).

Run by /imports/validate and /imports/upload before registering. The 6 checks
are reported as (pass | warn | fail) with a short description, matching the
UI "校验报告" card.
"""
from __future__ import annotations

import ast
import datetime as dt
import inspect
import io
import re
from typing import Any

from . import registry
from .base import Factor, FactorContext

# Static blacklist (low effort but useful even without sandbox, per design)
_BLACKLIST_MODULES = (
    "os",
    "sys",
    "subprocess",
    "socket",
    "shutil",
    "ctypes",
    "requests",
    "httpx",
    "urllib",
    "ftplib",
)
_BLACKLIST_BUILTINS = ("eval", "exec", "open", "input", "__import__")


def _has_blacklisted_import(tree: ast.AST) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in _BLACKLIST_MODULES:
                    hits.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            top = (node.module or "").split(".")[0]
            if top in _BLACKLIST_MODULES:
                hits.append(node.module or "")
    return hits


def _has_blacklisted_call(tree: ast.AST) -> list[str]:
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _BLACKLIST_BUILTINS:
            hits.append(node.id)
    return hits


def _find_factor_class(source: str) -> tuple[str | None, list[str]]:
    """Locate the first class that names Factor as a base (best-effort).

    Returns (class_name, all_class_names) so the report can warn if more than
    one factor class is declared in the same file.
    """
    tree = ast.parse(source)
    candidates: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for b in node.bases:
                if isinstance(b, ast.Name) and b.id in ("Factor", "BaseFactor"):
                    candidates.append(node.name)
                elif isinstance(b, ast.Attribute) and b.attr in (
                    "Factor",
                    "BaseFactor",
                ):
                    candidates.append(node.name)
    return (candidates[0] if candidates else None, candidates)


def _smoke_run(
    cls,
    reader,
    sample_syms: list[str] | None = None,
    days: int = 5,
) -> tuple[str, str]:
    """Try instantiating + calling compute on a small slice; return (status, detail).

    `status` in {"pass", "warn", "fail"}.
    """
    try:
        inst: Factor = cls()
    except Exception as e:  # noqa: BLE001
        return "fail", f"无法实例化 {cls.__name__}: {e!r}"
    # Pick 5 symbols and 5 days from the universe
    try:
        if sample_syms is None:
            syms_tbl = reader.read_symbols(active_only=True)
            import polars as pl

            syms = pl.from_arrow(syms_tbl).head(5)["symbol"].to_list()
        else:
            syms = list(sample_syms)[:5]
        if not syms:
            return "warn", "无样本股票可用 (reference.symbols 为空)"
        # Use last 14 calendar days as a window
        end = dt.date.today()
        start = end - dt.timedelta(days=21)
        ctx = FactorContext(reader, syms, start, end)
        if inst.requires_pit:
            out = inst.compute_cross_section(ctx, end)
        else:
            out = inst.compute(ctx)
        if out is None or out.height == 0:
            return "warn", "compute() 返回空表 (可能数据缺失或窗口不足)"
        return "pass", f"产出 {out.height} 行 · 列 {list(out.columns)[:4]}"
    except Exception as e:  # noqa: BLE001
        return "fail", f"compute() 抛错: {type(e).__name__}: {e}"


def validate_factor(
    source: str,
    *,
    reader=None,
    class_name_hint: str | None = None,
    declared_dependencies: list[str] | None = None,
) -> dict[str, Any]:
    """Run the 6 named checks. Returns a structured report.

    {
      "ok": bool,
      "class_name": str | None,
      "param_schema": dict,
      "requires_pit": bool,
      "dependencies": [str, ...],
      "code_sha256": str,
      "checks": [
        {"id": str, "label": str, "status": "pass"|"warn"|"fail", "detail": str},
        ...
      ],
      "summary": {"pass": n, "warn": n, "fail": n},
    }
    """
    import hashlib

    code_sha = hashlib.sha256(source.encode()).hexdigest()
    checks: list[dict[str, str]] = []

    # ── (1) Syntax / class detection ─────────────────────────────────────
    try:
        tree = ast.parse(source)
        class_name, all_classes = _find_factor_class(source)
        if class_name_hint:
            class_name = class_name_hint
        if class_name is None:
            checks.append(
                {
                    "id": "schema",
                    "label": "语法/结构 (compile + class 检测)",
                    "status": "fail",
                    "detail": "未发现继承 Factor / BaseFactor 的类",
                }
            )
            return _finalize(
                {}, None, source, code_sha, declared_dependencies or [], checks
            )
        warn_extra = (
            f" · 检出 {len(all_classes)} 个候选类,使用第一个"
            if len(all_classes) > 1
            else ""
        )
        checks.append(
            {
                "id": "schema",
                "label": "语法/结构 (compile + class 检测)",
                "status": "pass",
                "detail": f"类 {class_name} 继承 Factor{warn_extra}",
            }
        )
    except SyntaxError as e:
        checks.append(
            {
                "id": "schema",
                "label": "语法/结构 (compile + class 检测)",
                "status": "fail",
                "detail": f"SyntaxError: {e.msg} (line {e.lineno})",
            }
        )
        return _finalize({}, None, source, code_sha, declared_dependencies or [], checks)

    # ── (2) Parameter schema ─────────────────────────────────────────────
    cls = None
    try:
        cls = registry.instantiate_from_code(source, class_name).__class__
        param_schema = dict(getattr(cls, "param_schema", {}) or {})
        ps_desc = ", ".join(
            f"{k}: {t.__name__}={d}" for k, (t, d) in param_schema.items()
        )
        checks.append(
            {
                "id": "params",
                "label": "参数声明 schema",
                "status": "pass",
                "detail": f"识别出 {len(param_schema)} 个 @params ({ps_desc})"
                if param_schema
                else "无参数 (class 无 param_schema)",
            }
        )
    except Exception as e:  # noqa: BLE001
        checks.append(
            {
                "id": "params",
                "label": "参数声明 schema",
                "status": "warn",
                "detail": f"无法解析 param_schema: {e}",
            }
        )
        param_schema = {}

    # ── (3) Dependency table existence ───────────────────────────────────
    declared = list(declared_dependencies or [])
    if cls is not None:
        declared = list(getattr(cls, "dependencies", declared) or declared)
    if reader is not None and declared:
        try:
            from quant_data.schemas import ALL_TABLES

            missing = [d for d in declared if d not in ALL_TABLES]
            if not missing:
                checks.append(
                    {
                        "id": "deps",
                        "label": "依赖表存在性",
                        "status": "pass",
                        "detail": " · ".join(declared) + " · 可访问",
                    }
                )
            else:
                checks.append(
                    {
                        "id": "deps",
                        "label": "依赖表存在性",
                        "status": "fail",
                        "detail": f"未知表: {missing}",
                    }
                )
        except Exception as e:  # noqa: BLE001
            checks.append(
                {
                    "id": "deps",
                    "label": "依赖表存在性",
                    "status": "warn",
                    "detail": f"无法验证依赖: {e}",
                }
            )
    else:
        checks.append(
            {
                "id": "deps",
                "label": "依赖表存在性",
                "status": "warn" if not declared else "pass",
                "detail": " · ".join(declared) if declared else "未声明依赖",
            }
        )

    # ── (4) Fundamentals requirement ─────────────────────────────────────
    requires_pit = bool(cls is not None and getattr(cls, "requires_pit", False))
    checks.append(
        {
            "id": "fund",
            "label": "财报数据需求",
            "status": "pass",
            "detail": "需要财报 (PIT 切片)" if requires_pit else "不需要财报",
        }
    )

    # ── (5) Sample run smoke test ────────────────────────────────────────
    if cls is not None and reader is not None:
        st, det = _smoke_run(cls, reader)
        checks.append(
            {
                "id": "sample",
                "label": "抽样运行 (5 只 · 5 天)",
                "status": st,
                "detail": det,
            }
        )
    else:
        checks.append(
            {
                "id": "sample",
                "label": "抽样运行 (5 只 · 5 天)",
                "status": "warn",
                "detail": "未提供 reader,跳过 smoke test",
            }
        )

    # ── (6) Code safety scan ─────────────────────────────────────────────
    bl_imports = _has_blacklisted_import(tree)
    bl_calls = _has_blacklisted_call(tree)
    if bl_imports or bl_calls:
        checks.append(
            {
                "id": "code",
                "label": "代码安全扫描",
                "status": "warn",
                "detail": (
                    "黑名单命中: "
                    + ", ".join(bl_imports + bl_calls)
                    + " (测试阶段仅警告,生产前请清理)"
                ),
            }
        )
    else:
        checks.append(
            {
                "id": "code",
                "label": "代码安全扫描",
                "status": "pass",
                "detail": "0 高危调用 (eval/exec/import os 等)",
            }
        )

    return _finalize(param_schema, class_name, source, code_sha, declared, checks)


def _finalize(
    param_schema: dict,
    class_name: str | None,
    source: str,
    code_sha: str,
    deps: list[str],
    checks: list[dict],
) -> dict[str, Any]:
    summary = {
        "pass": sum(1 for c in checks if c["status"] == "pass"),
        "warn": sum(1 for c in checks if c["status"] == "warn"),
        "fail": sum(1 for c in checks if c["status"] == "fail"),
    }
    return {
        "ok": summary["fail"] == 0,
        "class_name": class_name,
        "param_schema": {
            k: {"type": t.__name__, "default": d} for k, (t, d) in param_schema.items()
        },
        "requires_pit": any(
            c["id"] == "fund" and "需要财报 (PIT" in c.get("detail", "") for c in checks
        ),
        "dependencies": deps,
        "code_sha256": code_sha,
        "checks": checks,
        "summary": summary,
    }
