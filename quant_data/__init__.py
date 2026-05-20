"""quant_data -- data layer for a multi-factor quant system (design doc v0.2).

Public surface:
    bootstrap(root)                     -- set up the data/ tree + metadata
    DataReader                          -- governed read access (current view, PIT)
    ingest                              -- ingestion orchestrator (per-table helpers)
    metadata.{batches,sources,field_registry}
    migration, governance, corporate, quality, compaction
"""
from __future__ import annotations

from . import (
    compaction,
    config,
    corporate,
    governance,
    ingest,
    invariants,
    metadata,
    migration,
    quality,
    schemas,
    storage,
    symbology,
)
from .bootstrap import bootstrap
from .reader import DataReader

__all__ = [
    "bootstrap",
    "DataReader",
    "ingest",
    "metadata",
    "migration",
    "governance",
    "corporate",
    "quality",
    "invariants",
    "symbology",
    "compaction",
    "schemas",
    "storage",
    "config",
]
