"""Unified error format (doc §5.1 / appendix B).

All API errors serialise as ``{"error": {code, message, details, trace_id}}``.
"""
from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .logging import app_logger
from .logging.context import trace_id_var

_log = app_logger

# code -> default HTTP status (appendix B)
ERROR_STATUS = {
    "INVALID_PARAMETER": 400,
    "MISSING_PARAMETER": 400,
    "INVALID_DATE_RANGE": 400,
    "UNAUTHENTICATED": 401,
    "PERMISSION_DENIED": 403,
    "NOT_FOUND": 404,                  # generic 404 (e.g. 监控告警/规则 not found)
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


def _trace_id(request: Request | None = None) -> str:
    """The active request's trace_id (set by TraceIdMiddleware), else a fresh one.

    Prefers ``request.state.trace_id`` (set on the scope, survives even when the
    context vars have been reset because the unhandled-exception handler runs in
    Starlette's ServerErrorMiddleware, outside our middleware), then the context
    var, then a fresh id. Reusing the request's id means the error body's trace_id
    is the same one the frontend sent -- so the user can paste it into the log
    centre and find the whole chain (doc §5.3 / §8.8.1).
    """
    if request is not None:
        sid = getattr(request.state, "trace_id", None)
        if sid:
            return sid
    return trace_id_var.get() or ("t_" + uuid.uuid4().hex[:16])


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(request: Request, exc: ApiError):
        trace_id = _trace_id(request)
        _log.warn(f"api.error.{exc.code}", trace_id=trace_id, route=request.url.path,
                  method=request.method, status_code=exc.status_code, message=exc.message,
                  code=exc.code, details=exc.details)
        return JSONResponse(
            status_code=exc.status_code,
            content=_body(exc.code, exc.message, exc.details, trace_id),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        trace_id = _trace_id(request)
        _log.warn("api.error.validation", trace_id=trace_id, route=request.url.path,
                  method=request.method, status_code=400, message="请求参数校验失败",
                  errors=exc.errors())
        return JSONResponse(
            status_code=400,
            content=_body("INVALID_PARAMETER", "请求参数校验失败",
                          {"errors": exc.errors()}, trace_id),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        # Log the real exception server-side keyed by trace_id; never leak
        # internals (paths/SQL/stack) to the client. The handler runs in
        # ServerErrorMiddleware (outside our middleware), so pass trace_id
        # explicitly from request.state rather than the (already-reset) context var.
        trace_id = _trace_id(request)
        _log.error("unhandled.exception", exc=exc, trace_id=trace_id,
                   route=request.url.path, method=request.method, status_code=500)
        return JSONResponse(
            status_code=500,
            content=_body("INTERNAL_ERROR", "服务器内部错误,请用 trace_id 联系运维", {}, trace_id),
        )


# convenience raisers ---------------------------------------------------------
def not_found(code: str, message: str, **details):
    raise ApiError(code, message, details)


def bad_request(message: str, code: str = "INVALID_PARAMETER", **details):
    raise ApiError(code, message, details)


def conflict(code: str, message: str, **details):
    raise ApiError(code, message, details)
