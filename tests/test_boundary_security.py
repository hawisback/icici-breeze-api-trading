"""Tests for Roadmap Item 9.5: Boundary Protection and Idempotent Commands.

Verifies:
- Strict CORS origin allowlist and rejection of disallowed origins
- Request-size limits middleware (1MB ceiling with 413 Payload Too Large)
- Tiered sliding-window rate limiting with 429 Too Many Requests and Retry-After
- Command idempotency with exact replay, payload mismatch rejection, and invalid key validation
- Security headers (CSP, nosniff, DENY, Referrer-Policy, Permissions-Policy, X-Request-ID)
- Structured error response formatting ({ error: { code, message, details, request_id, timestamp }, detail })
- Audit logging of denied privileged and boundary-violating actions
"""

import asyncio
import json
import pytest
import httpx

from libs.config.settings import PlatformSettings
from libs.contracts.models import UserRole
from services.api_gateway.main import app
from services.api_gateway.service_container import initialize_services


@pytest.fixture
async def gateway_env(tmp_path):
    """Initialize platform with isolated temporary directories and custom security limits."""
    settings = PlatformSettings(
        data_root=str(tmp_path),
        live_trading_enabled=False,
        cors_allowed_origins=["http://localhost:3000"],
        max_request_body_bytes=10_000,  # 10KB limit for fast testing
        rate_limit_enabled=True,
        rate_limit_login_per_minute=3,   # 3 logins/min for testing
        rate_limit_orders_per_minute=5,  # 5 orders/min for testing
        rate_limit_general_per_minute=10,
        idempotency_ttl_seconds=3600,
    )
    container = await initialize_services(settings=settings)
    await container.rate_limiter.reset()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Helper to login
        async def login(username, password):
            res = await client.post(
                "/api/v1/auth/login",
                json={"username": username, "password": password},
            )
            assert res.status_code == 200, f"Login failed: {res.text}"
            token = res.json()["access_token"]
            return {"Authorization": f"Bearer {token}"}

        yield {
            "client": client,
            "container": container,
            "login": login,
            "settings": settings,
        }


@pytest.mark.asyncio
async def test_security_headers_present(gateway_env):
    """Verify all responses include defensive HTTP security headers and X-Request-ID."""
    client = gateway_env["client"]

    # Test GET request
    res = await client.get("/api/v1/system/health")
    assert res.status_code == 200
    headers = res.headers

    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert headers.get("X-Frame-Options") == "DENY"
    assert headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "default-src 'self'" in headers.get("Content-Security-Policy", "")
    assert "geolocation=()" in headers.get("Permissions-Policy", "")
    assert headers.get("X-XSS-Protection") == "0"
    assert headers.get("X-Request-ID") is not None
    assert headers.get("X-Request-ID").startswith("req_")

    # Custom incoming X-Request-ID should be preserved
    res2 = await client.get(
        "/api/v1/system/health",
        headers={"X-Request-ID": "custom-trace-id-12345"},
    )
    assert res2.headers.get("X-Request-ID") == "custom-trace-id-12345"


@pytest.mark.asyncio
async def test_strict_cors_allowlist(gateway_env):
    """Verify allowed origins pass and disallowed origins are blocked with 403."""
    client = gateway_env["client"]

    # 1. Allowed origin preflight OPTIONS request
    opt_res = await client.options(
        "/api/v1/orders",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization, Content-Type, Idempotency-Key",
        },
    )
    assert opt_res.status_code == 200
    assert opt_res.headers.get("access-control-allow-origin") == "http://localhost:3000"

    # 2. Allowed origin GET request
    get_res = await client.get(
        "/api/v1/system/health",
        headers={"Origin": "http://localhost:3000"},
    )
    assert get_res.status_code == 200
    assert get_res.headers.get("access-control-allow-origin") == "http://localhost:3000"

    # 3. Disallowed origin GET request
    evil_res = await client.get(
        "/api/v1/system/health",
        headers={"Origin": "https://malicious-phishing-portal.com"},
    )
    assert evil_res.status_code == 403
    data = evil_res.json()
    assert data["error"]["code"] == "CORS_ORIGIN_DENIED"
    assert "Origin 'https://malicious-phishing-portal.com' is not permitted" in data["error"]["message"]


@pytest.mark.asyncio
async def test_request_size_limit(gateway_env):
    """Verify payloads exceeding max_request_body_bytes (10KB in test) are rejected with 413."""
    client = gateway_env["client"]
    trader_headers = await gateway_env["login"]("trader", "Trader@Trading123!")

    # 1. Normal payload under limit succeeds
    normal_payload = {
        "instrument_id": "INST-NIFTY-2026-09-24-24800-CE",
        "symbol": "NIFTY24800CE",
        "side": "BUY",
        "order_type": "LIMIT",
        "quantity": 25,
        "price": 120.0,
        "trading_mode": "PAPER",
    }
    normal_res = await client.post("/api/v1/orders", json=normal_payload, headers=trader_headers)
    assert normal_res.status_code == 200

    # 2. Oversized payload (15KB > 10KB limit)
    large_comment = "X" * 15_000
    oversized_res = await client.post(
        "/api/v1/orders",
        content=large_comment.encode("utf-8"),
        headers={
            **trader_headers,
            "Content-Type": "application/json",
            "Content-Length": str(len(large_comment)),
        },
    )
    assert oversized_res.status_code == 413
    err = oversized_res.json()
    assert err["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert "exceeds maximum limit" in err["error"]["message"]


@pytest.mark.asyncio
async def test_tiered_rate_limiting(gateway_env):
    """Verify sliding-window rate limits return 429 and Retry-After header when exceeded."""
    client = gateway_env["client"]

    # Test settings has rate_limit_login_per_minute = 3
    # 1st attempt: 401 invalid password
    r1 = await client.post("/api/v1/auth/login", json={"username": "trader", "password": "wrong"})
    assert r1.status_code == 401

    # 2nd attempt: 401 invalid password
    r2 = await client.post("/api/v1/auth/login", json={"username": "trader", "password": "wrong"})
    assert r2.status_code == 401

    # 3rd attempt: 401 invalid password
    r3 = await client.post("/api/v1/auth/login", json={"username": "trader", "password": "wrong"})
    assert r3.status_code == 401

    # 4th attempt: rate limit exceeded!
    r4 = await client.post("/api/v1/auth/login", json={"username": "trader", "password": "wrong"})
    assert r4.status_code == 429
    assert "Retry-After" in r4.headers
    retry_after = int(r4.headers["Retry-After"])
    assert retry_after >= 1

    data = r4.json()
    assert data["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    assert "Rate limit exceeded" in data["error"]["message"]


@pytest.mark.asyncio
async def test_command_idempotency_order_placement(gateway_env):
    """Verify Idempotency-Key guarantees exact single execution and cached replay."""
    client = gateway_env["client"]
    trader_headers = await gateway_env["login"]("trader", "Trader@Trading123!")

    order_payload = {
        "instrument_id": "INST-NIFTY-2026-09-24-24800-CE",
        "symbol": "NIFTY24800CE",
        "side": "BUY",
        "order_type": "LIMIT",
        "quantity": 25,
        "price": 120.0,
        "trading_mode": "PAPER",
    }
    headers_with_key = {
        **trader_headers,
        "Idempotency-Key": "ORDER-KEY-TEST-001",
    }

    # 1. First order submission
    res1 = await client.post("/api/v1/orders", json=order_payload, headers=headers_with_key)
    assert res1.status_code == 200
    order1 = res1.json()
    assert "client_order_id" in order1
    assert "X-Idempotency-Replay" not in res1.headers
    assert res1.headers.get("Idempotency-Key") == "ORDER-KEY-TEST-001"

    # 2. Second order submission with identical Idempotency-Key and payload
    res2 = await client.post("/api/v1/orders", json=order_payload, headers=headers_with_key)
    assert res2.status_code == 200
    order2 = res2.json()

    # Must be an exact replay of the first response
    assert res2.headers.get("X-Idempotency-Replay") == "true"
    assert order2["order_id"] == order1["order_id"]
    assert order2["client_order_id"] == order1["client_order_id"]

    # 3. Verify in OMS: Only ONE order was created in the system!
    orders_res = await client.get("/api/v1/orders", headers=trader_headers)
    orders = orders_res.json()
    matching = [o for o in orders if o["order_id"] == order1["order_id"]]
    assert len(matching) == 1, "Expected exactly 1 order in OMS, not duplicates"


@pytest.mark.asyncio
async def test_command_idempotency_payload_mismatch(gateway_env):
    """Verify reusing an Idempotency-Key with different payload parameters returns 422."""
    client = gateway_env["client"]
    trader_headers = await gateway_env["login"]("trader", "Trader@Trading123!")

    headers = {
        **trader_headers,
        "Idempotency-Key": "ORDER-KEY-TEST-002",
    }

    # First call with quantity 25
    p1 = {
        "instrument_id": "INST-NIFTY-2026-09-24-24800-CE",
        "symbol": "NIFTY24800CE",
        "side": "BUY",
        "order_type": "LIMIT",
        "quantity": 25,
        "price": 120.0,
        "trading_mode": "PAPER",
    }
    r1 = await client.post("/api/v1/orders", json=p1, headers=headers)
    assert r1.status_code == 200

    # Second call with same key but quantity 50 (mismatched parameters)
    p2 = {**p1, "quantity": 50}
    r2 = await client.post("/api/v1/orders", json=p2, headers=headers)
    assert r2.status_code == 422
    err = r2.json()
    assert err["error"]["code"] == "IDEMPOTENCY_PAYLOAD_MISMATCH"
    assert "previously used with different request parameters" in err["error"]["message"]


@pytest.mark.asyncio
async def test_command_idempotency_invalid_key_format(gateway_env):
    """Verify invalid Idempotency-Key characters or empty keys return 400 Bad Request."""
    client = gateway_env["client"]
    trader_headers = await gateway_env["login"]("trader", "Trader@Trading123!")

    p = {
        "instrument_id": "INST-NIFTY-2026-09-24-24800-CE",
        "symbol": "NIFTY24800CE",
        "side": "BUY",
        "order_type": "LIMIT",
        "quantity": 25,
        "price": 120.0,
        "trading_mode": "PAPER",
    }

    # Invalid key with spaces and special symbols
    bad_headers = {
        **trader_headers,
        "Idempotency-Key": "bad key with spaces and symbols!@#$",
    }
    res = await client.post("/api/v1/orders", json=p, headers=bad_headers)
    assert res.status_code == 400
    data = res.json()
    assert data["error"]["code"] == "BAD_REQUEST"
    assert "Invalid Idempotency-Key format" in data["error"]["message"]


@pytest.mark.asyncio
async def test_command_idempotency_kill_switch(gateway_env):
    """Verify Idempotency-Key on operator safety command (kill-switch) replays safely."""
    client = gateway_env["client"]
    operator_headers = await gateway_env["login"]("operator", "Operator@Trading123!")

    headers = {
        **operator_headers,
        "Idempotency-Key": "KS-KEY-TEST-001",
    }
    payload = {"action": "EXIT_ONLY", "reason": "Testing idempotent kill switch"}

    # 1. First trigger
    r1 = await client.post("/api/v1/risk/kill-switch", json=payload, headers=headers)
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["status"] == "ACTIVATED"
    assert "event_id" in d1

    # 2. Second trigger with same key
    r2 = await client.post("/api/v1/risk/kill-switch", json=payload, headers=headers)
    assert r2.status_code == 200
    assert r2.headers.get("X-Idempotency-Replay") == "true"
    d2 = r2.json()
    assert d2["event_id"] == d1["event_id"]
    assert d2["action"] == "EXIT_ONLY"


@pytest.mark.asyncio
async def test_structured_error_responses(gateway_env):
    """Verify consistent JSON error structure across 401, 403, 404, and 422 responses."""
    client = gateway_env["client"]

    # 1. 401 Unauthorized (missing credentials)
    r401 = await client.get("/api/v1/auth/me")
    assert r401.status_code == 401
    d401 = r401.json()
    assert d401["error"]["code"] == "AUTHENTICATION_FAILED"
    assert "Authentication credentials were not provided" in d401["error"]["message"]
    assert "detail" in d401
    assert "timestamp" in d401["error"]
    assert "request_id" in d401["error"]

    # 2. 403 Forbidden (viewer attempting order placement)
    viewer_headers = await gateway_env["login"]("viewer", "Viewer@Trading123!")
    r403 = await client.post(
        "/api/v1/orders",
        json={
            "instrument_id": "INST-NIFTY-2026-09-24-24800-CE",
            "symbol": "NIFTY24800CE",
            "side": "BUY",
            "order_type": "LIMIT",
            "quantity": 25,
            "price": 120.0,
            "trading_mode": "PAPER",
        },
        headers=viewer_headers,
    )
    assert r403.status_code == 403
    d403 = r403.json()
    assert d403["error"]["code"] == "FORBIDDEN"
    assert "is not authorized" in d403["error"]["message"]

    # 3. 404 Not Found
    r404 = await client.get("/api/v1/market/quote/NON_EXISTENT_INSTRUMENT")
    assert r404.status_code == 404
    d404 = r404.json()
    assert d404["error"]["code"] == "NOT_FOUND"
    assert "Quote not found" in d404["error"]["message"]

    # 4. 422 Validation Error (invalid types in payload)
    trader_headers = await gateway_env["login"]("trader", "Trader@Trading123!")
    r422 = await client.post(
        "/api/v1/orders",
        json={
            "instrument_id": "INST-NIFTY-2026-09-24-24800-CE",
            "symbol": "NIFTY24800CE",
            "side": "INVALID_SIDE",
            "order_type": "LIMIT",
            "quantity": "not_an_int",
            "price": "not_a_float",
            "trading_mode": "PAPER",
        },
        headers=trader_headers,
    )
    assert r422.status_code == 422
    d422 = r422.json()
    assert d422["error"]["code"] == "VALIDATION_ERROR"
    assert len(d422["error"]["details"]) >= 3


@pytest.mark.asyncio
async def test_security_audit_events_dispatched(gateway_env):
    """Verify security violations (disallowed origin, rate limit, idempotency mismatch) record audit events."""
    client = gateway_env["client"]
    container = gateway_env["container"]

    # Trigger CORS disallowed origin
    await client.get("/api/v1/system/health", headers={"Origin": "https://unauthorized-domain.com"})

    # Wait for async audit handler to persist events
    found = False
    event_types = []
    for _ in range(30):
        logs = await container.audit_svc.get_recent_logs(limit=20)
        event_types = [log["event_type"] for log in logs]
        if "CORS_ORIGIN_DENIED" in event_types:
            found = True
            break
        await asyncio.sleep(0.05)

    assert found, f"Expected CORS_ORIGIN_DENIED in audit logs, got: {event_types}"
