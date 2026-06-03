"""Direct tests for charts.get_bar_revisions (doc §4.6.2).

The revision band is computed by pushing the symbol filter + min/max date into
SQL (reader.batch_symbol_date_range); these lock in the band values and the
minute-frequency date-cast path (CAST(datetime AS DATE)).
"""
from __future__ import annotations

import datetime as dt

from quant_data import charts, ingest
from quant_data.metadata import batches

from .conftest import daily_bar, minute_bar

SYM = "000001.SZ"


def _invalidated_daily(root, symbol=SYM):
    days = [dt.date(2024, 1, d) for d in (2, 3, 4, 5)]
    res = ingest.ingest_bars_daily(
        records=[daily_bar(d, symbol=symbol) for d in days],
        apply_mapping=False, run_checks=False, root=root,
    )
    batches.invalidate_batch(res.batch_id, reason="test", root=root)
    return res.batch_id, days


def test_bar_revisions_band(root):
    bid, days = _invalidated_daily(root)
    out = charts.get_bar_revisions(SYM, "1d", root=root)
    assert len(out) == 1
    band = out[0]
    assert band["start"] == days[0] and band["end"] == days[-1]
    assert band["batch_id"] == bid and band["status"] == "invalidated"


def test_bar_revisions_window_clips_band(root):
    _invalidated_daily(root)
    out = charts.get_bar_revisions(
        SYM, "1d", start=dt.date(2024, 1, 3), end=dt.date(2024, 1, 4), root=root
    )
    assert len(out) == 1
    assert out[0]["start"] == dt.date(2024, 1, 3)
    assert out[0]["end"] == dt.date(2024, 1, 4)


def test_bar_revisions_other_symbol_empty(root):
    _invalidated_daily(root)
    assert charts.get_bar_revisions("600519.SH", "1d", root=root) == []


def test_bar_revisions_minute_frequency_date_cast(root):
    """Minute bars span multiple datetimes per day; the band must be the date
    range (CAST(datetime AS DATE)), not the raw timestamps."""
    day1, day2 = dt.date(2024, 1, 2), dt.date(2024, 1, 3)
    recs = []
    for day in (day1, day2):
        for minute in (0, 30, 90):
            recs.append(minute_bar(dt.datetime.combine(day, dt.time(9, 30)) +
                                   dt.timedelta(minutes=minute), symbol=SYM))
    res = ingest.ingest_bars_minute(records=recs, apply_mapping=False,
                                    run_checks=False, root=root)
    batches.invalidate_batch(res.batch_id, reason="test", root=root)
    out = charts.get_bar_revisions(SYM, "1m", root=root)
    assert len(out) == 1
    assert out[0]["start"] == day1 and out[0]["end"] == day2
