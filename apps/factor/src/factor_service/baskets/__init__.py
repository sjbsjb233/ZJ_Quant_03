"""因子篮子 (Basket) 子系统 — 因子层重构 v1.3.

Modules:
  keys        — window descriptor + common/item/comparable key system (§5)
  ledger      — append-only parquet ledger + settle/incremental plan (§7)
  orchestrator— multi-level task orchestration: advance() (§6)
  scheduler   — rolling tracking tick (§7.1)
  scoring     — filter DSL + robust z-score + greedy prune (§11)
"""
