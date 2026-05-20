"""Runtime configuration: data-root resolution + global constants."""
from __future__ import annotations

import os
from pathlib import Path

# Default on-disk layout (doc §2): data/{raw,derived,reference,metadata}/...
_ENV_VAR = "QUANT_DATA_ROOT"
_DEFAULT_ROOT = Path.cwd() / "data"

# Top-level namespaces. derived/ is a placeholder this period (doc §4).
NAMESPACES = ("raw", "derived", "reference", "metadata")

# derived/ sub-directories reserved now so the factor layer can slot in later
# without a schema migration (doc §4.1).
DERIVED_PLACEHOLDER_DIRS = (
    "derived/factors_daily",
    "derived/factors_minute",
    "derived/signals_daily",
    "derived/signals_minute",
    "derived/factor_metrics",
)

# Supported downsample targets for read_bars_minute (doc §6.4).
# value = DuckDB INTERVAL string for time_bucket().
MINUTE_FREQUENCIES: dict[str, str] = {
    "1m": "1 minute",
    "5m": "5 minutes",
    "15m": "15 minutes",
    "30m": "30 minutes",
    "1h": "1 hour",
}

# A-share trading sessions, used by minute-bar timestamp validation (doc §3.2).
A_SHARE_SESSIONS = (("09:30", "11:30"), ("13:00", "15:00"))

# Audit retention window before compaction may physically remove dead files
# (doc §1.3 / §9.4). Removal is always manual.
AUDIT_RETENTION_DAYS = 90

# Field-promotion thresholds (doc §1.2 / §2.3).
PROMOTE_MIN_AGE_DAYS = 30


def data_root(root: str | os.PathLike | None = None) -> Path:
    """Resolve the data root: explicit arg > $QUANT_DATA_ROOT > ./data."""
    if root is not None:
        return Path(root)
    env = os.environ.get(_ENV_VAR)
    if env:
        return Path(env)
    return _DEFAULT_ROOT
