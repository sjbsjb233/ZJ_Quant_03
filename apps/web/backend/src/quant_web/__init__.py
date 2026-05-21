"""quant_web -- FastAPI monitoring/management platform for the quant data layer.

Wraps the ``quant_data`` package (data governance) behind a typed HTTP API:
data management (batches/sources/fields/tables/quality/maintenance/audit),
visualization (K-line / fundamentals / explorer), an async job runner, JWT auth
and an audit log. See the v0.2 design doc, appendix F.
"""
from __future__ import annotations

__all__ = ["create_app"]


def create_app():  # lazy import keeps `import quant_web` cheap
    from .main import create_app as _create_app

    return _create_app()
