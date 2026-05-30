"""Source connectors: akshare (主源) + tushare (备源).

Both import their underlying library lazily so dev/CI installs (which skip the
``[ingest]`` extra) can still import keeper_service; a fetch attempt without the
library raises a clear, actionable error.
"""
