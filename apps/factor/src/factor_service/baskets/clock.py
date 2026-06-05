"""Basket clock — the single source of "today"/"now" for scheduler + runner.

Honors the FACTOR_FAKE_TODAY test hook (config.factor_fake_today) so the tracking
tick + window resolution can be deterministically time-travelled in tests. With
the hook unset (production), these return the real wall clock. Record-only
timestamps (started_at/finished_at) may also use now() for frozen-time
consistency, but it's not required.
"""
from __future__ import annotations

import datetime as dt

from ..config import get_settings


def today() -> dt.date:
    return get_settings().fake_today_date or dt.date.today()


def now() -> dt.datetime:
    return get_settings().fake_now_dt or dt.datetime.now()
