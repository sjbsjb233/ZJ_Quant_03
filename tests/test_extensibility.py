"""Three-stage field extensibility: extras -> promoted -> core (doc §1.2, §7.3)."""
from __future__ import annotations

import datetime as dt

import pyarrow.parquet as pq

from quant_data import ingest, migration, storage
from quant_data.metadata import field_registry
from quant_data.schemas import get_spec

from .conftest import fundamental


def _ingest_roe(root, value="15.32"):
    ingest.ingest_fundamentals(records=[fundamental(_extras={"roe": value})], root=root)


def test_stage1_extras_zero_cost(root, reader):
    # a brand-new field needs no schema change; it lands in _extras and is readable
    _ingest_roe(root)
    t = reader.read_fundamentals(["000001.SZ"], as_of_date=dt.date(2025, 1, 1),
                                 extras_as_columns={"roe": "DOUBLE"})
    assert t.column("roe").to_pylist() == [15.32]
    assert field_registry.get_field("raw.fundamentals", "roe", root=root)["status"] == "extras"


def test_stage2_promoted_does_not_touch_schema(root, reader):
    _ingest_roe(root)
    field_registry.promote_field("raw.fundamentals", "roe", "promoted",
                                 consumers=["factor.roe_decile"], root=root)
    # still stored in _extras
    f = storage.list_data_files(get_spec("raw.fundamentals"), root=root)[0]
    extras = dict(pq.read_table(f).to_pylist()[0]["_extras"])
    assert "roe" in extras
    assert field_registry.get_field("raw.fundamentals", "roe", root=root)["status"] == "promoted"


def test_stage3_migration_materialises_core_column(root, reader):
    _ingest_roe(root)
    summary = migration.promote_field_to_core("raw.fundamentals", "roe", root=root)
    assert summary["files_rewritten"] == 1
    # physical file now has a real roe column and the key is gone from _extras
    f = storage.list_data_files(get_spec("raw.fundamentals"), root=root)[0]
    row = pq.read_table(f).to_pylist()[0]
    assert row["roe"] == 15.32
    assert dict(row["_extras"]) == {}
    assert field_registry.get_field("raw.fundamentals", "roe", root=root)["status"] == "core"


def test_compat_layer_after_migration(root, reader):
    # old code using extras_as_columns must keep working post-migration (doc §7.3)
    _ingest_roe(root)
    migration.promote_field_to_core("raw.fundamentals", "roe", root=root)
    t = reader.read_fundamentals(["000001.SZ"], as_of_date=dt.date(2025, 1, 1),
                                 extras_as_columns={"roe": "DOUBLE"})
    assert t.column("roe").to_pylist() == [15.32]


def test_mixed_pre_and_post_migration_rows(root, reader):
    # one report ingested before migration, another after -- both must read back
    ingest.ingest_fundamentals(records=[fundamental(symbol="A", _extras={"roe": "10.0"})], root=root)
    migration.promote_field_to_core("raw.fundamentals", "roe", root=root)
    ingest.ingest_fundamentals(records=[fundamental(symbol="B", _extras={"roe": "20.0"})], root=root)
    t = reader.read_fundamentals(["A", "B"], as_of_date=dt.date(2025, 1, 1),
                                 extras_as_columns={"roe": "DOUBLE"})
    got = {r["symbol"]: r["roe"] for r in t.to_pylist()}
    assert got == {"A": 10.0, "B": 20.0}


def test_migration_records_audit_batch(root):
    from quant_data.metadata import batches
    _ingest_roe(root)
    summary = migration.promote_field_to_core("raw.fundamentals", "roe", root=root)
    b = batches.get_batch(summary["batch_id"], root=root)
    assert b["source"] == "internal:schema-migration" and b["status"] == "success"


def test_deprecation_marks_status(root):
    _ingest_roe(root)
    field_registry.update_field_status("raw.fundamentals", "roe", "deprecated", root=root)
    f = field_registry.get_field("raw.fundamentals", "roe", root=root)
    assert f["status"] == "deprecated" and f["deprecated_at"] is not None
