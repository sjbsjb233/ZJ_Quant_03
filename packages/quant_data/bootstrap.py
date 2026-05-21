"""One-shot environment setup (doc §8 stage 1A).

Creates the data/ tree, initialises empty metadata ledgers so the current-view
queries always have something to read, and (optionally) registers the default
akshare source with its bars_daily field mapping (doc §2.2).
"""
from __future__ import annotations

from . import storage
from .metadata import sources
from .schemas import METADATA_TABLES

# Default akshare column mapping (doc §2.2 example).
AKSHARE_FIELD_MAPPING = {
    "raw.bars_daily": {
        "日期": "date",
        "代码": "symbol",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
    }
}


def init_metadata(root=None) -> None:
    """Create empty metadata parquet files if they don't exist yet."""
    for spec in METADATA_TABLES.values():
        if not storage.single_file_path(spec, root).exists():
            storage.write_rows(spec, [], root)


def bootstrap(root=None, register_defaults: bool = True) -> None:
    """Idempotent: lay out directories, init metadata, register akshare."""
    storage.ensure_layout(root)
    init_metadata(root)
    if register_defaults and sources.get_source("akshare", root) is None:
        sources.register_source(
            "akshare",
            display_name="掘金 akshare",
            category="external_api",
            priority=100,
            field_mapping=AKSHARE_FIELD_MAPPING,
            auth_method="none",
            description="默认外部行情源",
            root=root,
        )
