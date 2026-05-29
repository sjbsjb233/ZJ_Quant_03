"""quant_factor — factor computation, evaluation, and correlation.

This package is pure compute; it has no FastAPI / DB dependencies and is meant
to be importable both from the factor service (apps/factor) and from notebooks.

Public surface:
  - Factor / FactorContext       : the contract a factor implements (base)
  - FactorEvaluator              : end-to-end IC / IR / layered backtest
  - EvalConfig                   : the full RunConfig spec (design v2.0 §10)
  - resolve_universe             : turn a Universe row into per-day symbols
  - correlation_matrix           : multi-factor decorrelation analysis
  - winsorize_mad, zscore        : preprocess primitives
  - neutralize_cross_section, industry_at_level, ln_size
  - forward_returns              : strict t -> t+h forward returns
  - builtin: Momentum, LogMktCap (small reference set; more registered later)
  - registry: discover_builtins, instantiate_factor
"""
from __future__ import annotations

__version__ = "0.1.0"

from .base import Factor, FactorContext, RunCancelled
from .config import EvalConfig
from .preprocess import winsorize_mad, winsorize_quantile, zscore
from .neutralize import (
    industry_at_level,
    ln_size,
    neutralize_cross_section,
)
from .returns import forward_returns
from .universe import (
    PRESET_FLAG,
    PRESET_UNIVERSES,
    UniverseDef,
    resolve_universe,
)
from .evaluate import FactorEvaluator
from .correlation import correlation_matrix
from . import builtin, registry  # noqa: F401  (side-effect import for registry)

__all__ = [
    "Factor",
    "FactorContext",
    "RunCancelled",
    "EvalConfig",
    "winsorize_mad",
    "winsorize_quantile",
    "zscore",
    "industry_at_level",
    "ln_size",
    "neutralize_cross_section",
    "forward_returns",
    "PRESET_FLAG",
    "PRESET_UNIVERSES",
    "UniverseDef",
    "resolve_universe",
    "FactorEvaluator",
    "correlation_matrix",
    "builtin",
    "registry",
]
