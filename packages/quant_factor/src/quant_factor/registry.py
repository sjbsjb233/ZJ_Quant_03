"""Factor registry — discover builtin factors + safe instantiation.

Builtins live under quant_factor.builtin.*; this module imports them eagerly
and exposes them by (factor_id, base_name) lookup. The factor service uses
this to:
  - bootstrap builtins on first launch
  - resolve a (base_name, params) -> Factor instance when running a job
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Any, Type

from .base import Factor


_BUILTINS: dict[str, Type[Factor]] = {}


def register(cls: Type[Factor]) -> Type[Factor]:
    """Decorator used by builtin factor modules to opt-in to the registry."""
    name = cls.__name__.lower()
    _BUILTINS[name] = cls
    return cls


def discover_builtins() -> dict[str, Type[Factor]]:
    """Eagerly import all submodules under quant_factor.builtin so any
    `@register` decorators run. Returns {base_name: class}.

    Safe to call multiple times.
    """
    from . import builtin

    for _finder, modname, _ispkg in pkgutil.iter_modules(builtin.__path__):
        importlib.import_module(f"{builtin.__name__}.{modname}")
    return dict(_BUILTINS)


def instantiate(base_name: str, params: dict[str, Any] | None = None) -> Factor:
    """Look up a registered builtin and instantiate it with `params`."""
    discover_builtins()
    cls = _BUILTINS.get(base_name)
    if cls is None:
        raise KeyError(f"Unknown builtin factor {base_name!r}")
    return cls(**(params or {}))


def instantiate_from_code(
    source: str, class_name: str, params: dict[str, Any] | None = None
) -> Factor:
    """Run Python source in an isolated namespace, find `class_name`, return
    an instance. Used by the import path when starting fresh from uploaded
    code (no sandbox in v2.1 testing phase per user instruction).
    """
    ns: dict[str, Any] = {}
    code = compile(source, f"<uploaded:{class_name}>", "exec")
    # Make Factor / FactorContext accessible to factor authors
    from . import base as _base

    ns.update(
        {
            "Factor": _base.Factor,
            "FactorContext": _base.FactorContext,
            "RunCancelled": _base.RunCancelled,
        }
    )
    exec(code, ns)
    obj = ns.get(class_name)
    if obj is None or not inspect.isclass(obj):
        raise KeyError(f"Uploaded code defines no class {class_name!r}")
    return obj(**(params or {}))


def all_registered() -> dict[str, Type[Factor]]:
    discover_builtins()
    return dict(_BUILTINS)
