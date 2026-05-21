"""Unified error format (doc §5.1 / appendix B).

All API errors serialise as ``{"error": {code, message, details, trace_id}}``.
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# code -> default HTTP status (appendix B)
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
    """Raise to return a structured error response."""

    def __init__(self, code: str, message: str, details: dict | None = None,
                 status_code: int | None = None):
        self.code = code
        self.message = message
        self.details = details or {}
        self.status_code = status_code or ERROR_STATUS.get(code, 400)
        super().__init__(message)


def _body(code: str, message: str, details: dict, trace_id: str) -> dict:
    return {"error": {"code": code, "message": message, "details": details, "trace_id": trace_id}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(_: Request, exc: ApiError):
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(exc.code, exc.message, exc.details, uuid.uuid4().hex),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=400,
            content=_body("INVALID_PARAMETER", "请求参数校验失败",
                          {"errors": exc.errors()}, uuid.uuid4().hex),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content=_body("INTERNAL_ERROR", str(exc) or "服务器内部错误", {}, uuid.uuid4().hex),
        )


# convenience raisers ---------------------------------------------------------
def not_found(code: str, message: str, **details):
    raise ApiError(code, message, details)


def bad_request(message: str, code: str = "INVALID_PARAMETER", **details):
    raise ApiError(code, message, details)


def conflict(code: str, message: str, **details):
    raise ApiError(code, message, details)
