"""Structured error model.

Every API failure is returned as JSON:
{"ok": false, "error_code": "...", "message": "...", "details": ..., "agent": ..., "run_id": ...}
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from .logging_setup import get_logger, redact

log = get_logger("errors")

HTTP_ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_FAILED",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    502: "UPSTREAM_ERROR",
    503: "SERVICE_UNAVAILABLE",
    504: "UPSTREAM_TIMEOUT",
}


class QAError(Exception):
    """Raise from services and agents; rendered as a structured JSON error."""

    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        status: int = 400,
        details: Any = None,
        agent: str | None = None,
        run_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message
        self.status = status
        self.details = details
        self.agent = agent
        self.run_id = run_id

    def to_dict(self) -> dict[str, Any]:
        return error_body(self.error_code, self.message, self.details, self.agent, self.run_id)


def error_body(
    error_code: str,
    message: str,
    details: Any = None,
    agent: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "error_code": error_code,
        "message": message,
        "details": redact(details),
        "agent": agent,
        "run_id": run_id,
    }


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(QAError)
    async def _qa_error(_: Request, exc: QAError) -> JSONResponse:
        return JSONResponse(exc.to_dict(), status_code=exc.status)

    @app.exception_handler(HTTPException)
    async def _http_error(_: Request, exc: HTTPException) -> JSONResponse:
        code = HTTP_ERROR_CODES.get(exc.status_code, "HTTP_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else code.replace("_", " ").title()
        details = None if isinstance(exc.detail, str) else exc.detail
        return JSONResponse(error_body(code, message, details), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            error_body("VALIDATION_FAILED", "Request validation failed.", exc.errors()),
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled server error")
        return JSONResponse(
            error_body("INTERNAL_ERROR", "Unexpected server error.", type(exc).__name__),
            status_code=500,
        )
