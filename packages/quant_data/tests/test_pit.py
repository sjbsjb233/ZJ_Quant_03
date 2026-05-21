"""Point-In-Time semantics for fundamentals (doc §3.3, §6.2)."""
from __future__ import annotations

import datetime as dt

import pytest

from quant_data import ingest

from .conftest import fundamental


def test_pit_hides_unpublished_reports(root, reader):
    # report_period 2024-09-30 but published 2024-10-25
    ingest.ingest_fundamentals(records=[fundamental(publish_date=dt.date(2024, 10, 25))], root=root)
    assert reader.read_fundamentals(["000001.SZ"], as_of_date=dt.date(2024, 10, 24)).num_rows == 0
    assert reader.read_fundamentals(["000001.SZ"], as_of_date=dt.date(2024, 10, 25)).num_rows == 1


def test_pit_uses_publish_date_not_report_period(root, reader):
    # a report whose period ended long ago but was only just published must not
    # be visible before its publish_date
    rec = fundamental(report_period=dt.date(2024, 6, 30), period_type="Q2",
                      publish_date=dt.date(2025, 11, 16))
    ingest.ingest_fundamentals(records=[rec], root=root)
    assert reader.read_fundamentals(["000001.SZ"], as_of_date=dt.date(2025, 11, 15)).num_rows == 0


def test_as_of_date_is_required(root, reader):
    with pytest.raises(ValueError):
        reader.read_fundamentals(["000001.SZ"])


def test_pit_with_extras_column(root, reader):
    ingest.ingest_fundamentals(records=[fundamental(_extras={"roe": "15.32"})], root=root)
    t = reader.read_fundamentals(["000001.SZ"], as_of_date=dt.date(2024, 12, 31),
                                 extras_as_columns={"roe": "DOUBLE"})
    assert t.column("roe").to_pylist() == [15.32]
