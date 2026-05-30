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
    charts,
    compaction,
    config,
    corporate,
    governance,
    ingest,
    invariants,
    log_shipper,
    logging,
    metadata,
    migration,
    quality,
    quality_log,
    schemas,
    storage,
    symbology,
    table_stats,
)
from .bootstrap import bootstrap
from .log_shipper import HttpLogShipper
from .reader import DataReader

__all__ = [
    "bootstrap",
    "DataReader",
    "HttpLogShipper",
    "log_shipper",
    "ingest",
    "logging",
    "metadata",
    "migration",
    "governance",
    "corporate",
    "quality",
    "quality_log",
    "table_stats",
    "charts",
    "invariants",
    "symbology",
    "compaction",
    "schemas",
    "storage",
    "config",
]
