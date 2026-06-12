"""WP6 — CPU 配额(《内存治理总体方案》WP6).

PRAGMA threads 注入 / env 覆盖 / 核÷槽推导 / 独占线程提升 contextvar。
"""
from __future__ import annotations

from quant_data import config


def _threads_of(con) -> int:
    return int(con.execute("SELECT current_setting('threads')").fetchone()[0])


def test_wp6_explicit_env_overrides(monkeypatch):
    monkeypatch.setenv(config.DUCKDB_THREADS_ENV, "3")
    con = config.connect_duckdb()
    assert _threads_of(con) == 3
    con.close()


def test_wp6_auto_derives_cores_over_slots(monkeypatch):
    monkeypatch.setenv(config.DUCKDB_THREADS_ENV, "auto")
    monkeypatch.setenv(config.HEAVY_TASK_SLOTS_ENV, str(config.available_cores()))
    assert config.duckdb_threads() == 1            # 核 ÷ 核 = 1,消 N× 超卖
    con = config.connect_duckdb()
    assert _threads_of(con) == 1
    con.close()
    monkeypatch.setenv(config.HEAVY_TASK_SLOTS_ENV, "1")
    assert config.duckdb_threads() == config.available_cores()


def test_wp6_exclusive_override_contextvar(monkeypatch):
    """WP5 v1.2 独占 CPU 提升:override 只影响本任务上下文,reset 即恢复。"""
    monkeypatch.setenv(config.DUCKDB_THREADS_ENV, "2")
    assert config.duckdb_threads() == 2
    tok = config.set_duckdb_thread_override(config.available_cores())
    try:
        assert config.duckdb_threads() == config.available_cores()
        con = config.connect_duckdb()
        assert _threads_of(con) == config.available_cores()
        con.close()
    finally:
        config.reset_duckdb_thread_override(tok)
    assert config.duckdb_threads() == 2


def test_wp6_memory_limit_pragma_still_applied(monkeypatch):
    # WP6 不改既有内存护栏 —— threads 与 memory_limit 同连接共存
    monkeypatch.setenv(config.DUCKDB_MEMORY_LIMIT_ENV, "123MB")
    con = config.connect_duckdb()
    mem = con.execute("SELECT current_setting('memory_limit')").fetchone()[0]
    assert "117.3" in mem or "123" in mem  # duckdb 以 GiB/MiB 规范化显示
    con.close()


def test_chunk_tables_env(monkeypatch):
    assert "raw.bars_minute" in config.chunk_tables()
    monkeypatch.setenv(config.CHUNK_TABLES_ENV, "raw.a, raw.b")
    assert config.chunk_tables() == {"raw.a", "raw.b"}
