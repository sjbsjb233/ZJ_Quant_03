"""DB migration test (设计 §12.1): adding the degradation columns + table to a
factor.db created BEFORE the feature shipped must be in-place and idempotent,
with historical rows defaulting to ``none`` / 0."""
from __future__ import annotations

import os
import sqlite3


def test_migrate_adds_degradation_columns_to_old_db(tmp_path):
    dbp = tmp_path / "old.db"
    con = sqlite3.connect(dbp)
    con.execute(
        "CREATE TABLE factor_run (run_id TEXT PRIMARY KEY, factor_id TEXT, "
        "version TEXT, status TEXT)"
    )
    con.execute(
        "CREATE TABLE eval_summary (id INTEGER PRIMARY KEY, run_id TEXT, factor_id TEXT)"
    )
    con.execute("INSERT INTO factor_run VALUES ('frun_old','mom','v1','success')")
    con.commit()
    con.close()

    prev_url = os.environ.get("FACTOR_DB_URL")
    os.environ["FACTOR_DB_URL"] = f"sqlite:///{dbp}"
    os.environ["FACTOR_STORE_ROOT"] = str(tmp_path)
    try:
        from factor_service.config import reset_settings_cache
        reset_settings_cache()
        from factor_service.db import base as b
        b.rebind(os.environ["FACTOR_DB_URL"])
        b.init_db()           # create_all + _migrate_added_columns
        b.init_db()           # idempotent — must not error on second run

        con = sqlite3.connect(dbp)
        fr = {r[1] for r in con.execute("PRAGMA table_info(factor_run)")}
        es = {r[1] for r in con.execute("PRAGMA table_info(eval_summary)")}
        tables = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        row = con.execute(
            "SELECT degradation_level, degradation_count, has_critical FROM factor_run"
        ).fetchone()
        con.close()
    finally:
        if prev_url is not None:
            os.environ["FACTOR_DB_URL"] = prev_url

    assert {"degradation_level", "degradation_count", "has_critical", "preflight"} <= fr
    assert {"degradation_level", "degradation_count", "has_critical"} <= es
    assert "factor_run_degradation" in tables
    # historical row defaults (设计 §12.1)
    assert row == ("none", 0, 0)
