"""data-keeper (数据策管服务) — the single writer of the data lake.

One process: a write-command FastAPI app + an in-process JobRunner thread pool
(heavy parquet rewrites, ingest, quality) + an in-process scheduler thread
(autonomous crawl / quality / maintenance). See 容器化重构 V3 §4.2.
"""

__version__ = "0.1.0"
