"""ApiError mirror of quant_web — same wire shape:
   {"error": {"code", "message", "details", "trace_id"}}

Keeps the frontend's error parsing identical across the two services.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .logging_ctx import current_trace_id


_ERROR_STATUS = {
    "INVALID_PARAMETER": 400,
    "UNAUTHENTICATED": 401,
    "PERMISSION_DENIED": 403,
    "NOT_FOUND": 404,
    "FACTOR_NOT_FOUND": 404,
    "RUN_NOT_FOUND": 404,
    "TASK_NOT_FOUND": 404,
    "UNIVERSE_NOT_FOUND": 404,
    "VERSION_NOT_FOUND": 404,
    "CONFLICT": 409,
    "ALREADY_EXISTS": 409,
    "PRECONDITION_FAILED": 412,
    "INTERNAL": 500,
    "NOT_IMPLEMENTED": 501,
}


class ApiError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        status_code: int | None = None,
    ):
        self.code = code
        self.message = message
        self.details = details or {}
        self.status_code = status_code or _ERROR_STATUS.get(code, 400)
        super().__init__(message)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):  # noqa: ARG001
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                    "trace_id": current_trace_id(),
                }
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):  # noqa: ARG001
        # Bubble up validation errors cleanly; everything else -> 500.
        from fastapi.exceptions import RequestValidationError

        if isinstance(exc, RequestValidationError):
            return JSONResponse(
                status_code=400,
                content={
                    "error": {
                        "code": "INVALID_PARAMETER",
                        "message": "请求参数校验失败",
                        "details": {"errors": exc.errors()},
                        "trace_id": current_trace_id(),
                    }
                },
            )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL",
                    "message": str(exc) or "Internal Server Error",
                    "details": {"type": type(exc).__name__},
                    "trace_id": current_trace_id(),
                }
            },
        )


def not_found(code: str, message: str, **details):
    raise ApiError(code, message, details)


def bad_request(message: str, code: str = "INVALID_PARAMETER", **details):
    raise ApiError(code, message, details)


def conflict(code: str, message: str, **details):
    raise ApiError(code, message, details)
