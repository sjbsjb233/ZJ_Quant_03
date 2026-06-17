"""ApiError mirror of quant_web — same wire shape:
   {"error": {"code", "message", "details", "trace_id"}}

Keeps the frontend's error parsing identical across the two services.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .logging_ctx import current_trace_id

_log = logging.getLogger(__name__)


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
    # 因子篮子 (因子层重构 §12) ──────────────────────────────────────────────
    "BASKET_NOT_FOUND": 404,
    "SNAPSHOT_NOT_FOUND": 404,
    "SELECTION_NOT_FOUND": 404,
    "PRESET_NOT_FOUND": 404,
    "BRUN_NOT_FOUND": 404,
    "SNAPSHOT_MISMATCH": 422,
    "SELECTION_FINALIZED": 409,
    "BASKET_STALE_CONFIG": 409,
    "TRACKING_ON_FIXED_WINDOW": 422,
    "DEGRADED_INPUT_BLOCKED": 422,
    "PREFLIGHT_BLOCKED": 422,
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

    # FastAPI 自带的 RequestValidationError 不会冒泡到 Exception 处理器
    # (它在 FastAPI 初始化时单独注册),必须显式登记才能套用统一 {"error": {...}}
    # 信封,否则校验失败会退回默认 422 {"detail": [...]},前端共用的错误解析失效。
    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):  # noqa: ARG001
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

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):  # noqa: ARG001
        # 服务端记录真实异常(经 obs HttpLogShipper 送入中心 app_log,按 trace_id 检索),
        # 但绝不把内部细节(SQL/路径/栈/异常类型)泄露给客户端 —— 对外只回通用文案。
        trace_id = current_trace_id()
        _log.error(
            "unhandled.exception",
            exc_info=exc,
            extra={
                "trace_id": trace_id,
                "route": request.url.path,
                "method": request.method,
                "status_code": 500,
            },
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL",
                    "message": "服务器内部错误,请用 trace_id 联系运维",
                    "details": {},
                    "trace_id": trace_id,
                }
            },
        )


def not_found(code: str, message: str, **details):
    raise ApiError(code, message, details)


def bad_request(message: str, code: str = "INVALID_PARAMETER", **details):
    raise ApiError(code, message, details)


def conflict(code: str, message: str, **details):
    raise ApiError(code, message, details)
