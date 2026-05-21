"""Visualization routes (doc §4.6) -- K-line / fundamentals / explorer / symbols."""
from __future__ import annotations

from fastapi import APIRouter

from .explorer import router as explorer_router
from .fundamentals import router as fundamentals_router
from .kline import router as kline_router
from .symbols import router as symbols_router

router = APIRouter(prefix="/charts", tags=["charts"])
for _r in (kline_router, fundamentals_router, explorer_router, symbols_router):
    router.include_router(_r)
