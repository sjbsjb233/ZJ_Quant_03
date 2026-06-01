"""Cache-key data fingerprint (设计 §9).

The risk §9 guards against: a cache keyed purely on the *config* returns the same
key whether or not the data is complete — so a degraded result computed while
data was missing would be re-served even after the data is补齐.

:func:`data_fingerprint` derives a short hash of the *data availability* relevant
to a run (sector-classification presence + market-cap/vwap column presence). Fold
it into :func:`quant_factor.config.config_signature` via its ``extra`` argument:

    sig = config_signature(cfg, extra=[data_fingerprint(reader, cfg)])

When the underlying data changes (sectors imported, circ_mv backfilled), the
fingerprint changes → the signature changes → any cache keyed on it misses and
the run is recomputed (设计 §9.1). It is intentionally cheap + best-effort: a
fingerprint failure degrades to ``"fp:err"`` rather than blocking submission.
"""
from __future__ import annotations

import hashlib

import polars as pl


def data_fingerprint(reader, cfg) -> str:
    """Short hash of data availability relevant to `cfg` (设计 §9.1)."""
    parts: list[str] = []
    try:
        sec = pl.from_arrow(
            reader.read_sectors(classification_system=cfg.classification_system)
        )
        parts.append(f"sec:{cfg.classification_system}:{sec.height}")
    except Exception:  # noqa: BLE001
        parts.append("sec:err")
    try:
        # one tiny bars read to learn which factor-relevant columns are present
        bars = pl.from_arrow(
            reader.read_bars_daily(start=cfg.start, end=cfg.start, adjust="none")
        )
        cols = set(bars.columns)
        parts.append("circ:" + ("1" if "circ_mv" in cols else "0"))
        parts.append("totl:" + ("1" if "total_mv" in cols else "0"))
        parts.append("vwap:" + ("1" if "vwap" in cols else "0"))
    except Exception:  # noqa: BLE001
        parts.append("bars:err")
    digest = hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]
    return f"fp:{digest}"
