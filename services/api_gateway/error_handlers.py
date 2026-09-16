"""Standardized, structured error handlers for FastAPI API Gateway.

Ensures all client-visible errors conform to:
{
    "error": {
        "code": "...",
        "message": "...",
        "details": [...],
        "request_id": "...",
        "timestamp": "..."
    },
    "detail": "..."  # preserved for backward compatibility
}
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from libs.contracts.models import generate_id, utc_now

logger = logging.getLogger(__name__)


def _get_request_id(request: Request) -> str:
    """Retrieve existing request ID from state or generate a new one."""
    return getattr(request.state, "request_id", None) or f"req_{generate_id()[:12]}"


def _map_http_status_to_code(status_code: int, detail: Any) -> str:
    detail_str = str(detail).upper() if detail else ""

    if status_code == status.HTTP_400_BAD_REQUEST:
        return "BAD_REQUEST"
    elif status_code == status.HTTP_401_UNAUTHORIZED:
        return "AUTHENTICATION_FAILED"
    elif status_code == status.HTTP_403_FORBIDDEN:
        if "CORS" in detail_str or "ORIGIN" in detail_str:
            return "CORS_ORIGIN_DENIED"
        return "FORBIDDEN"
    elif status_code == status.HTTP_404_NOT_FOUND:
        return "NOT_FOUND"
    elif status_code in (409, status.HTTP_409_CONFLICT):
        if "IN PROGRESS" in detail_str:
            return "IDEMPOTENCY_IN_PROGRESS"
        return "IDEMPOTENCY_CONFLICT" if "IDEMPOTENCY" in detail_str else "CONFLICT"
    elif status_code == 413:
        return "PAYLOAD_TOO_LARGE"
    elif status_code == 422:
        if "IDEMPOTENCY" in detail_str or "MISMATCH" in detail_str or "DIFFERENT" in detail_str:
            return "IDEMPOTENCY_PAYLOAD_MISMATCH"
        return "VALIDATION_ERROR"
    elif status_code == status.HTTP_429_TOO_MANY_REQUESTS:
        return "RATE_LIMIT_EXCEEDED"
    elif status_code >= 500:
        return "INTERNAL_SERVER_ERROR"
    return "HTTP_ERROR"


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle Pydantic request validation errors."""
    request_id = _get_request_id(request)
    details = []
    for err in exc.errors():
        field = " -> ".join(str(loc) for loc in err.get("loc", []))
        details.append({
            "field": field,
            "issue": err.get("msg", "Invalid parameter"),
            "type": err.get("type", "value_error"),
        })

    message = f"Validation failed for {len(details)} field(s)."
    error_payload = {
        "error": {
            "code": "VALIDATION_ERROR",
            "message": message,
            "details": details,
            "request_id": request_id,
            "timestamp": utc_now().isoformat(),
        },
        "detail": message,
    }
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=error_payload,
        headers={"X-Request-ID": request_id},
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Handle explicit HTTP exceptions."""
    request_id = _get_request_id(request)
    code = _map_http_status_to_code(exc.status_code, exc.detail)
    message = str(exc.detail) if exc.detail else "An HTTP error occurred."

    error_payload = {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
            "timestamp": utc_now().isoformat(),
        },
        "detail": message,
    }
    headers = dict(exc.headers) if exc.headers else {}
    headers["X-Request-ID"] = request_id

    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload,
        headers=headers,
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected 500 exceptions securely without leaking internals."""
    request_id = _get_request_id(request)
    logger.exception("Unhandled server exception for request %s: %s", request_id, exc)

    error_payload = {
        "error": {
            "code": "INTERNAL_SERVER_ERROR",
            "message": "An unexpected internal server error occurred.",
            "request_id": request_id,
            "timestamp": utc_now().isoformat(),
        },
        "detail": "An unexpected internal server error occurred.",
    }
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_payload,
        headers={"X-Request-ID": request_id},
    )


def register_error_handlers(app: FastAPI) -> None:
    """Register custom exception handlers with FastAPI application."""
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(Exception, generic_exception_handler)
