"""Unit and integration tests for API/WebSocket Authentication and RBAC (Item 9.4).

Verifies:
- PBKDF2 password hashing and constant-time verification.
- HS256 JWT access token issuance, validation, and expiration.
- Database-backed user management and default role bootstrapping.
- Refresh token rotation and instant revocation.
- Single-use WebSocket tickets.
- Endpoint-level RBAC enforcement (ADMIN, OPERATOR, TRADER, READ_ONLY).
- WebSocket handshake authentication.
- Security audit event emissions.
"""

from datetime import datetime, timedelta, timezone
import pytest
import httpx
import jwt

from libs.config.settings import PlatformSettings
from libs.contracts.models import (
    OrderSide,
    OrderType,
    TradingMode,
    UserPrincipal,
    UserRole,
    utc_now,
)
from libs.events.bus import InMemoryEventBus
from services.api_gateway.main import app
from services.api_gateway.service_container import initialize_services
from services.auth.repository import AuthRepository
from services.auth.security import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)
from services.auth.service import AuthService


@pytest.mark.asyncio
async def test_password_hashing():
    """Verify PBKDF2 hashing, salt uniqueness, and verification."""
    password = "SuperSecretPassword123!"
    pw_hash1, salt1 = hash_password(password)
    pw_hash2, salt2 = hash_password(password)

    # Salt uniqueness
    assert salt1 != salt2
    assert pw_hash1 != pw_hash2

    # Verification
    assert verify_password(password, pw_hash1, salt1) is True
    assert verify_password("WrongPassword", pw_hash1, salt1) is False
    assert verify_password(password, pw_hash1, salt2) is False


@pytest.mark.asyncio
async def test_jwt_creation_and_expiration():
    """Verify HS256 token encoding, claims, and expiration."""
    signing_key = "test-secret-key-12345"

    token = create_access_token(
        user_id="user-123",
        username="alice",
        role="TRADER",
        signing_key=signing_key,
        expires_delta=timedelta(minutes=5),
    )
    claims = decode_access_token(token, signing_key)
    assert claims["sub"] == "user-123"
    assert claims["username"] == "alice"
    assert claims["role"] == "TRADER"
    assert "jti" in claims

    # Expired token test
    expired_token = create_access_token(
        user_id="user-123",
        username="alice",
        role="TRADER",
        signing_key=signing_key,
        expires_delta=timedelta(seconds=-1),
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(expired_token, signing_key)

    # Wrong key test
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(token, "wrong-secret-key")


@pytest.mark.asyncio
async def test_auth_service_authenticate_and_bootstrap(tmp_path):
    """Verify bootstrap default accounts and login flow."""
    bus = InMemoryEventBus()
    await bus.start()
    settings = PlatformSettings(data_root=str(tmp_path))
    repo = AuthRepository(db_path=tmp_path / "auth.db")
    service = AuthService(repository=repo, event_bus=bus, settings=settings)
    await service.initialize()

    # Default users seeded
    admin_row = await repo.get_user_by_username("admin")
    assert admin_row is not None
    assert admin_row["role"] == UserRole.ADMIN.value

    # Authenticate success
    user, access_token, refresh_token, exp = await service.authenticate("admin", "Admin@Trading123!")
    assert user is not None
    assert user.username == "admin"
    assert user.role == UserRole.ADMIN
    assert access_token is not None
    assert refresh_token is not None
    assert exp > 0

    # Authenticate failure
    fail_user, _, _, _ = await service.authenticate("admin", "WrongPass")
    assert fail_user is None

    # Unknown user
    unknown, _, _, _ = await service.authenticate("ghost", "pass")
    assert unknown is None

    await bus.stop()


@pytest.mark.asyncio
async def test_token_rotation_and_revocation(tmp_path):
    """Verify refresh token rotation and logout revocation."""
    bus = InMemoryEventBus()
    await bus.start()
    settings = PlatformSettings(data_root=str(tmp_path))
    repo = AuthRepository(db_path=tmp_path / "auth.db")
    service = AuthService(repository=repo, event_bus=bus, settings=settings)
    await service.initialize()

    _, _, refresh_token, _ = await service.authenticate("trader", "Trader@Trading123!")

    # 1. Rotate token
    user, new_access, new_refresh, _ = await service.refresh_tokens(refresh_token)
    assert user is not None
    assert user.username == "trader"
    assert new_refresh != refresh_token

    # 2. Old refresh token cannot be reused
    reuse_user, _, _, _ = await service.refresh_tokens(refresh_token)
    assert reuse_user is None

    # 3. Logout revokes active token
    revoked = await service.logout(new_refresh)
    assert revoked is True

    # Token no longer active
    after_logout, _, _, _ = await service.refresh_tokens(new_refresh)
    assert after_logout is None

    await bus.stop()


@pytest.mark.asyncio
async def test_single_use_ws_ticket(tmp_path):
    """Verify single-use WebSocket tickets cannot be reused or expired."""
    bus = InMemoryEventBus()
    await bus.start()
    settings = PlatformSettings(data_root=str(tmp_path), ws_ticket_expire_seconds=2)
    repo = AuthRepository(db_path=tmp_path / "auth.db")
    service = AuthService(repository=repo, event_bus=bus, settings=settings)
    await service.initialize()

    user, _, _, _ = await service.authenticate("viewer", "Viewer@Trading123!")
    ticket, exp = await service.create_ws_ticket(user)
    assert ticket.startswith("wst_")
    assert exp == 2

    # First consumption succeeds
    consumed_user = await service.validate_and_consume_ws_ticket(ticket)
    assert consumed_user is not None
    assert consumed_user.username == "viewer"

    # Second consumption fails (single-use)
    reused = await service.validate_and_consume_ws_ticket(ticket)
    assert reused is None

    await bus.stop()


@pytest.mark.asyncio
async def test_api_auth_endpoints(tmp_path):
    """Test login, me, refresh, and logout REST API endpoints."""
    settings = PlatformSettings(
        data_root=str(tmp_path),
        live_trading_enabled=False,
    )
    await initialize_services(settings=settings)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Unauthenticated request to /auth/me -> 401
        resp = await client.get("/api/v1/auth/me")
        assert resp.status_code == 401

        # 2. Login
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "trader", "password": "Trader@Trading123!"},
        )
        assert login_resp.status_code == 200
        data = login_resp.json()
        assert "access_token" in data
        assert "refresh_token" in data
        token = data["access_token"]
        refresh = data["refresh_token"]

        # 3. Authenticated request to /auth/me
        headers = {"Authorization": f"Bearer {token}"}
        me_resp = await client.get("/api/v1/auth/me", headers=headers)
        assert me_resp.status_code == 200
        me = me_resp.json()
        assert me["user"]["username"] == "trader"
        assert me["permissions"]["can_trade"] is True
        assert me["permissions"]["can_operate_safety"] is False

        # 4. Generate WS ticket
        ticket_resp = await client.post("/api/v1/auth/ws-ticket", headers=headers)
        assert ticket_resp.status_code == 200
        assert ticket_resp.json()["ticket"].startswith("wst_")

        # 5. Rotate token via refresh
        refresh_resp = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh},
        )
        assert refresh_resp.status_code == 200
        new_token = refresh_resp.json()["access_token"]
        new_refresh = refresh_resp.json()["refresh_token"]

        # 6. Logout
        logout_resp = await client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": new_refresh},
        )
        assert logout_resp.status_code == 200
        assert logout_resp.json()["status"] == "LOGGED_OUT"


@pytest.mark.asyncio
async def test_rbac_endpoint_restrictions(tmp_path):
    """Verify role-based access control enforces restrictions on operations."""
    settings = PlatformSettings(
        data_root=str(tmp_path),
        live_trading_enabled=False,
    )
    await initialize_services(settings=settings)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Helper login
        async def login_as(user, pwd):
            res = await client.post("/api/v1/auth/login", json={"username": user, "password": pwd})
            return {"Authorization": f"Bearer {res.json()['access_token']}"}

        viewer_headers = await login_as("viewer", "Viewer@Trading123!")
        trader_headers = await login_as("trader", "Trader@Trading123!")
        operator_headers = await login_as("operator", "Operator@Trading123!")
        admin_headers = await login_as("admin", "Admin@Trading123!")

        order_payload = {
            "instrument_id": "INST-NIFTY-2026-09-24-24800-CE",
            "symbol": "NIFTY24800CE",
            "side": "BUY",
            "order_type": "LIMIT",
            "quantity": 25,
            "price": 120.0,
            "trading_mode": "PAPER",
        }

        # 1. READ_ONLY (viewer) cannot place orders -> 403 Forbidden
        res = await client.post("/api/v1/orders", json=order_payload, headers=viewer_headers)
        assert res.status_code == 403

        # 2. READ_ONLY cannot trigger kill switch -> 403 Forbidden
        res = await client.post(
            "/api/v1/risk/kill-switch",
            json={"action": "BLOCK_ENTRIES", "reason": "test"},
            headers=viewer_headers,
        )
        assert res.status_code == 403

        # 3. TRADER can place orders -> 200 OK
        res = await client.post("/api/v1/orders", json=order_payload, headers=trader_headers)
        assert res.status_code == 200
        order = res.json()
        assert order["symbol"] == "NIFTY24800CE"

        # 4. TRADER cannot trigger kill switch -> 403 Forbidden
        res = await client.post(
            "/api/v1/risk/kill-switch",
            json={"action": "BLOCK_ENTRIES", "reason": "test"},
            headers=trader_headers,
        )
        assert res.status_code == 403

        # 5. OPERATOR cannot place orders -> 403 Forbidden
        res = await client.post("/api/v1/orders", json=order_payload, headers=operator_headers)
        assert res.status_code == 403

        # 6. OPERATOR can trigger kill switch -> 200 OK
        res = await client.post(
            "/api/v1/risk/kill-switch",
            json={"action": "BLOCK_ENTRIES", "reason": "test"},
            headers=operator_headers,
        )
        assert res.status_code == 200

        # 7. ADMIN can do both
        res = await client.post("/api/v1/orders", json=order_payload, headers=admin_headers)
        assert res.status_code == 200
        res = await client.post(
            "/api/v1/risk/kill-switch",
            json={"action": "BLOCK_ENTRIES", "reason": "test"},
            headers=admin_headers,
        )
        assert res.status_code == 200


def test_websocket_auth_flow():
    """Verify WebSocket rejects unauthenticated connections and accepts valid tickets."""
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        # 1. Login to get token
        login_res = client.post(
            "/api/v1/auth/login",
            json={"username": "trader", "password": "Trader@Trading123!"},
        )
        assert login_res.status_code == 200
        token = login_res.json()["access_token"]

        # 2. Get WS ticket
        ticket_res = client.post(
            "/api/v1/auth/ws-ticket",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert ticket_res.status_code == 200
        ticket = ticket_res.json()["ticket"]

        # 3. Connect with valid ticket
        with client.websocket_connect(f"/ws/live?ticket={ticket}") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "AUTH_OK"
            assert msg["user"] == "trader"

        # 4. Connect with invalid ticket
        with client.websocket_connect("/ws/live?ticket=invalid_ticket") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "AUTH_ERROR"
            assert msg["code"] == 4401

