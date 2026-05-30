"""Crawler + ingest pipeline (V3 §4.3).

quant_data.ingest already exposes a ``fetch_fn=`` hook on every per-table helper;
"爬虫 = 实现一个 fetch_fn 喂给它们". The adapters here implement that hook against
akshare (主源) and tushare (备源); :mod:`pipeline` wires them with the
akshare→tushare fallback and runs the full append-only ingest (which triggers the
built-in quality checks + batch registration on the same run / trace_id).
"""
