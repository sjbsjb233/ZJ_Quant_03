"""Data-management module routes (doc §4 / §5) under /api/v1/data."""
from __future__ import annotations

from fastapi import APIRouter

from ..audit.routes import router as audit_router
from ..jobs.routes import router as jobs_router
from .batches import router as batches_router
from .charts import router as charts_router
from .dashboard import router as dashboard_router
from .fields import router as fields_router
from .maintenance import router as maintenance_router
from .quality import router as quality_router
from .sources import router as sources_router
from .tables import router as tables_router

router = APIRouter()
for _r in (
    dashboard_router,
    batches_router,
    sources_router,
    fields_router,
    tables_router,
    charts_router,
    quality_router,
    maintenance_router,
    jobs_router,
    audit_router,
):
    router.include_router(_r)
