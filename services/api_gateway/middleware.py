"""API Gateway Boundary Protection Middlewares.

Provides:
- SecurityHeadersMiddleware (CSP, nosniff, DENY, X-Request-ID)
- RequestSizeLimitMiddleware (1MB ceiling, payload-too-large rejection)
- RateLimitMiddleware (sliding-window tiered rate limiting with Retry-After)
- StrictOriginMiddleware (CORS boundary enforcement and origin validation)
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
import jwt
from starlette.middleware.base import BaseHTTPMiddleware

from libs.config.settings import PlatformSettings, get_platform_settings
from libs.contracts.models import generate_id, utc_now
from libs.events.bus import EventEnvelope, Topics
from services.api_gateway.rate_limiter import RateLimiter
from services.api_gateway.service_container import get_services

logger = logging.getLogger(__name__)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Enforces standard defensive HTTP security headers on all gateway responses."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Generate or capture trace request ID
        request_id = request.headers.get("X-Request-ID") or f"req_{generate_id()[:12]}"
        request.state.request_id = request_id

        response = await call_next(request)

        # Attach defense-in-depth headers
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=(), payment=()"
        response.headers["X-XSS-Protection"] = "0"
        response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'; base-uri 'self';"

        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Rejects incoming HTTP requests whose payload exceeds max_request_body_bytes."""

    def __init__(self, app, settings: Optional[PlatformSettings] = None) -> None:
        super().__init__(app)
        self.settings = settings or get_platform_settings()

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        try:
            services = get_services()
            max_bytes = services.settings.max_request_body_bytes
        except Exception:
            max_bytes = self.settings.max_request_body_bytes

        content_length = request.headers.get("content-length")

        if content_length:
            try:
                length = int(content_length)
                if length > max_bytes:
                    request_id = getattr(request.state, "request_id", f"req_{generate_id()[:12]}")
                    logger.warning(
                        "Request %s exceeded payload ceiling: %d bytes (limit: %d bytes)",
                        request_id,
                        length,
                        max_bytes,
                    )

                    # Emit security audit event
                    try:
                        services = get_services()
                        await services.event_bus.publish(
                            EventEnvelope(
                                topic=Topics.AUDIT_EVENT,
                                payload={
                                    "event_type": "PAYLOAD_SIZE_EXCEEDED",
                                    "client_ip": request.client.host if request.client else "UNKNOWN",
                                    "path": request.url.path,
                                    "content_length": length,
                                    "limit": max_bytes,
                                    "timestamp": utc_now().isoformat(),
                                },
                            )
                        )
                    except Exception:
                        pass

                    return JSONResponse(
                        status_code=413,
                        content={
                            "error": {
                                "code": "PAYLOAD_TOO_LARGE",
                                "message": f"Request payload ({length} bytes) exceeds maximum limit of {max_bytes} bytes.",
                                "request_id": request_id,
                                "timestamp": utc_now().isoformat(),
                            },
                            "detail": f"Request payload exceeds maximum limit of {max_bytes} bytes.",
                        },
                        headers={"X-Request-ID": request_id},
                    )
            except ValueError:
                pass

        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Enforces sliding-window rate limiting per client identifier and endpoint tier."""

    def __init__(self, app, rate_limiter: RateLimiter, settings: Optional[PlatformSettings] = None) -> None:
        super().__init__(app)
        self.limiter = rate_limiter
        self.settings = settings or get_platform_settings()

    def _determine_tier(self, path: str) -> str:
        if path.startswith("/api/v1/auth") or path == "/api/v1/session/login":
            return "AUTH"
        elif (
            path.startswith("/api/v1/orders")
            or path.startswith("/api/v1/risk")
            or path.startswith("/api/v1/live-gate")
        ):
            return "COMMANDS"
        return "GENERAL"

    def _get_client_id(self, request: Request) -> str:
        # Check Bearer token sub if present
        auth_header = request.headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.split(" ", 1)[1]
            try:
                # Decode unverified token to extract sub/user_id for rate limiting bucket
                unverified = jwt.decode(token, options={"verify_signature": False})
                if "sub" in unverified:
                    return f"user:{unverified['sub']}"
            except Exception:
                pass

        # Fallback to IP address
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return f"ip:{forwarded.split(',')[0].strip()}"
        if request.client and request.client.host:
            return f"ip:{request.client.host}"
        return "ip:127.0.0.1"

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        try:
            services = get_services()
            limiter = services.rate_limiter
            settings = services.settings
        except Exception:
            limiter = self.limiter
            settings = self.settings

        if not settings.rate_limit_enabled or request.method == "OPTIONS":
            return await call_next(request)

        path = request.url.path
        # Ignore docs/health/openapi endpoints from throttling
        if path in ["/docs", "/openapi.json", "/redoc", "/api/v1/system/health"]:
            return await call_next(request)

        tier = self._determine_tier(path)
        client_id = self._get_client_id(request)

        allowed, retry_after = await limiter.check_rate_limit(client_id=client_id, tier=tier)
        if not allowed:
            request_id = getattr(request.state, "request_id", f"req_{generate_id()[:12]}")
            logger.warning(
                "Rate limit exceeded for %s on tier %s (path %s). Retry-After: %d",
                client_id,
                tier,
                path,
                retry_after,
            )

            # Emit audit event
            try:
                services = get_services()
                await services.event_bus.publish(
                    EventEnvelope(
                        topic=Topics.AUDIT_EVENT,
                        payload={
                            "event_type": "RATE_LIMIT_EXCEEDED",
                            "client_id": client_id,
                            "tier": tier,
                            "path": path,
                            "retry_after": retry_after,
                            "timestamp": utc_now().isoformat(),
                        },
                    )
                )
            except Exception:
                pass

            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": {
                        "code": "RATE_LIMIT_EXCEEDED",
                        "message": f"Rate limit exceeded for tier '{tier}'. Please retry in {retry_after} seconds.",
                        "retry_after": retry_after,
                        "request_id": request_id,
                        "timestamp": utc_now().isoformat(),
                    },
                    "detail": f"Rate limit exceeded. Retry in {retry_after} seconds.",
                },
                headers={
                    "X-Request-ID": request_id,
                    "Retry-After": str(retry_after),
                },
            )

        return await call_next(request)


class StrictOriginMiddleware(BaseHTTPMiddleware):
    """Enforces strict CORS origin allowlist, rejecting unauthorized origins."""

    def __init__(self, app, settings: Optional[PlatformSettings] = None) -> None:
        super().__init__(app)
        self.settings = settings or get_platform_settings()
        self.allowed_origins = set(self.settings.cors_allowed_origins)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        try:
            services = get_services()
            allowed = set(services.settings.cors_allowed_origins)
        except Exception:
            allowed = self.allowed_origins

        origin = request.headers.get("origin")
        # If Origin is sent by a browser, validate it against allowlist
        if origin and origin not in allowed:
            request_id = getattr(request.state, "request_id", f"req_{generate_id()[:12]}")
            logger.warning("CORS origin disallowed: %s (path %s)", origin, request.url.path)

            try:
                services = get_services()
                await services.event_bus.publish(
                    EventEnvelope(
                        topic=Topics.AUDIT_EVENT,
                        payload={
                            "event_type": "CORS_ORIGIN_DENIED",
                            "disallowed_origin": origin,
                            "path": request.url.path,
                            "client_ip": request.client.host if request.client else "UNKNOWN",
                            "timestamp": utc_now().isoformat(),
                        },
                    )
                )
            except Exception:
                pass

            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={
                    "error": {
                        "code": "CORS_ORIGIN_DENIED",
                        "message": f"Origin '{origin}' is not permitted by CORS policy.",
                        "request_id": request_id,
                        "timestamp": utc_now().isoformat(),
                    },
                    "detail": f"Origin '{origin}' is not permitted by CORS policy.",
                },
                headers={"X-Request-ID": request_id},
            )

        return await call_next(request)
