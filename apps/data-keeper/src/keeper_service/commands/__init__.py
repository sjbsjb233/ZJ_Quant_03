"""Write-command routers (V3 §4.1 写). Mounted at keeper root with the SAME
sub-paths as quant_web's data router (/sources, /fields, /batches, /quality,
/maintenance) so the web proxy forwards /api/v1/data/<x> -> keeper /<x> verbatim.
"""
from __future__ import annotations

from fastapi import APIRouter

from .batches import router as batches_router
from .fields import router as fields_router
from .maintenance import router as maintenance_router
from .quality import router as quality_router
from .sources import router as sources_router

command_router = APIRouter()
for _r in (sources_router, fields_router, batches_router, quality_router, maintenance_router):
    command_router.include_router(_r)
