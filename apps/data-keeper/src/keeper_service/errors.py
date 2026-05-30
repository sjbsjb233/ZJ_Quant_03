"""ApiError mirror of quant_web.errors — identical wire shape so the frontend's
error parsing stays the same whether a write was handled locally by web or
forwarded to data-keeper:

    {"error": {"code", "message", "details", "trace_id"}}

The status-code map is copied verbatim from quant_web so a forwarded 404/409/400
keeps its exact status when the web proxy relays it back to the browser.
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from quant_data import logging as qd_logging

from .logging_ctx import current_trace_id

_log = qd_logging.get_logger("keeper.error")

# code -> default HTTP status (must match quant_web.errors.ERROR_STATUS).
ERROR_STATUS = {
    "INVALID_PARAMETER": 400,
    "MISSING_PARAMETER": 400,
    "INVALID_DATE_RANGE": 400,
    "UNAUTHENTICATED": 401,
    "PERMISSION_DENIED": 403,
    "BATCH_NOT_FOUND": 404,
    "FIELD_NOT_FOUND": 404,
    "SOURCE_NOT_FOUND": 404,
    "JOB_NOT_FOUND": 404,
    "TABLE_NOT_FOUND": 404,
    "ISSUE_NOT_FOUND": 404,
    "SNAPSHOT_NOT_FOUND": 404,
    "TEMPLATE_NOT_FOUND": 404,
    "USER_NOT_FOUND": 404,
    "SYMBOL_NOT_FOUND": 404,
    "INVALID_STATE_TRANSITION": 409,
    "CYCLE_DETECTED": 409,
    "MIGRATION_IN_PROGRESS": 409,
    "DUPLICATE_RESOURCE": 409,
    "INVALID_NEW_BATCH": 409,
    "UNPROCESSABLE_ENTITY": 422,
    "INTERNAL_ERROR": 500,
    "NOT_IMPLEMENTED": 501,
    "SERVICE_UNAVAILABLE": 503,
}


class ApiError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None,
                 status_code: int | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        self.status_code = status_code or ERROR_STATUS.get(code, 400)
        super().__init__(message)


def _body(code: str, message: str, details: dict, trace_id: str) -> dict:
    return {"error": {"code": code, "message": message, "details": details, "trace_id": trace_id}}


def _trace_id() -> str:
    return current_trace_id() or ("t_" + uuid.uuid4().hex[:16])


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(request: Request, exc: ApiError):  # noqa: ARG001
        trace_id = _trace_id()
        _log.warn(f"api.error.{exc.code}", route=request.url.path, method=request.method,
                  status_code=exc.status_code, message=exc.message, code=exc.code,
                  details=exc.details)
        return JSONResponse(status_code=exc.status_code,
                            content=_body(exc.code, exc.message, exc.details, trace_id))

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):  # noqa: ARG001
        trace_id = _trace_id()
        return JSONResponse(
            status_code=400,
            content=_body("INVALID_PARAMETER", "请求参数校验失败", {"errors": exc.errors()}, trace_id),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        trace_id = _trace_id()
        _log.error("unhandled.exception", exc=exc, route=request.url.path,
                   method=request.method, status_code=500)
        return JSONResponse(
            status_code=500,
            content=_body("INTERNAL_ERROR", "数据策管服务内部错误,请用 trace_id 联系运维", {}, trace_id),
        )
