"""In-process job subsystem (thread pool + keeper.db Job ledger).

Ported from quant_web.jobs (the runner was always a ThreadPoolExecutor, never a
separate container — V3 §4.2.1) with one addition: the job's trace_id is
persisted at enqueue and re-bound into quant_data.logging when the worker thread
executes, so the whole chain (button → command → file rewrite, or scheduler →
crawl → ingest → quality → batch) shares one trace_id (V3 §6.4).
"""
