"""Unit and integration tests for Server-Side LIVE Trading Activation Gate (Item 7.4).

Verifies:
- Live gate is disabled by default (Fail-Closed).
- Two-step confirmation challenge generation and token verification.
- Rejection on token mismatch, operator mismatch, or challenge expiry.
- Account allowlisting enforcement.
- Authorization window auto-expiration.
- Emergency manual revocation.
- Pre-trade RiskService fail-closed rejection of unauthorized LIVE orders.
- Full API Gateway REST endpoints flow (challenge -> confirm -> status -> revoke).
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from libs.config.settings import PlatformSettings
from libs.contracts.models import (
    OrderIntent,
    OrderSide,
    OrderType,
    ProductType,
    TimeInForce,
    TradingMode,
    utc_now,
)
from libs.events.bus import InMemoryEventBus
from services.api_gateway.main import app
from services.api_gateway.service_container import initialize_services
from services.risk.live_gate import LiveTradingGate
from services.risk.repository import RiskRepository
from services.risk.service import RiskService


def _live_capable_settings(**overrides):
    values = {
        "live_trading_enabled": True,
        "live_allowed_accounts": ["ICICI_PRIMARY", "ICICI_SECONDARY"],
        "auth_signing_key": "live-gate-test-signing-key-32-bytes-minimum",
        "market_data_backend": "breeze",
        "breeze_api_key": "test-live-key",
        "breeze_secret_key": "test-live-secret",
    }
    values.update(overrides)
    return PlatformSettings(**values)


@pytest.fixture
def clean_gate():
    bus = InMemoryEventBus()
    settings = _live_capable_settings()
    return LiveTradingGate(settings=settings, event_bus=bus)


@pytest.mark.asyncio
async def test_live_gate_default_disabled(clean_gate):
    """Verify gate starts disabled and blocks all live orders by default."""
    gate = clean_gate
    status = gate.get_status()
    assert status["live_authorized"] is False
    assert status["system_setting_enabled"] is True
    assert gate.is_live_active() is False

    allowed, reason = gate.validate_live_order("ICICI_PRIMARY")
    assert allowed is False
    assert "not authorized" in reason.lower()


@pytest.mark.asyncio
async def test_server_capability_disabled_rejects_activation_challenge():
    gate = LiveTradingGate(
        settings=PlatformSettings(
            live_trading_enabled=False,
            live_allowed_accounts=["ICICI_PRIMARY"],
        ),
        event_bus=InMemoryEventBus(),
    )
    with pytest.raises(PermissionError, match="LIVE_TRADING_ENABLED is false"):
        await gate.request_activation_challenge(
            operator_id="ADMIN_ALICE",
            account_id="ICICI_PRIMARY",
            duration_minutes=30,
        )


@pytest.mark.asyncio
async def test_challenge_cannot_expand_account_allowlist(clean_gate):
    with pytest.raises(PermissionError, match="not in LIVE_ALLOWED_ACCOUNTS"):
        await clean_gate.request_activation_challenge(
            operator_id="ADMIN_ALICE",
            account_id="UNAUTHORIZED_ACCOUNT_99",
            duration_minutes=30,
        )


@pytest.mark.asyncio
async def test_challenge_and_successful_confirmation(clean_gate):
    """Verify standard two-step challenge and confirmation workflow."""
    gate = clean_gate

    # Step 1: Request challenge
    challenge_data = await gate.request_activation_challenge(
        operator_id="ADMIN_ALICE",
        account_id="ICICI_PRIMARY",
        duration_minutes=60,
    )
    assert "challenge_id" in challenge_data
    assert "challenge_token" in challenge_data
    assert challenge_data["challenge_token"].startswith("CONFIRM-")

    c_id = challenge_data["challenge_id"]
    token = challenge_data["challenge_token"]

    # Step 2: Confirm challenge
    confirmed = await gate.confirm_activation(
        challenge_id=c_id,
        challenge_token=token,
        operator_id="ADMIN_ALICE",
    )
    assert confirmed is True
    assert gate.is_live_active() is True

    status = gate.get_status()
    assert status["live_authorized"] is True
    assert status["time_remaining_sec"] > 3500  # ~3600 sec for 60 min
    assert status["allowed_account_count"] == 2
    assert "allowed_accounts" not in status

    # Order validation now passes for allowed account
    allowed, msg = gate.validate_live_order("ICICI_PRIMARY")
    assert allowed is True
    assert msg == "Authorized"


@pytest.mark.asyncio
async def test_confirmation_failures(clean_gate):
    """Verify security rejections during step 2 confirmation."""
    gate = clean_gate

    challenge = await gate.request_activation_challenge(
        operator_id="ADMIN_ALICE",
        account_id="ICICI_PRIMARY",
        duration_minutes=15,
    )
    c_id = challenge["challenge_id"]
    token = challenge["challenge_token"]

    # Failure 1: Non-existent challenge ID
    assert await gate.confirm_activation("INVALID_ID", token, "ADMIN_ALICE") is False

    # Failure 2: Incorrect token
    assert await gate.confirm_activation(c_id, "WRONG_TOKEN", "ADMIN_ALICE") is False

    # Failure 3: Operator mismatch
    assert await gate.confirm_activation(c_id, token, "HACKER_BOB") is False

    # Failure 4: Expired challenge
    gate._pending_challenges[c_id]["expires_at"] = utc_now() - timedelta(seconds=1)
    assert await gate.confirm_activation(c_id, token, "ADMIN_ALICE") is False

    # Gate remains disabled
    assert gate.is_live_active() is False


@pytest.mark.asyncio
async def test_account_allowlist_enforcement(clean_gate):
    """Verify only allowlisted accounts can place orders even when LIVE is active."""
    gate = clean_gate

    challenge = await gate.request_activation_challenge(
        operator_id="ADMIN_ALICE",
        account_id="ICICI_PRIMARY",
        duration_minutes=30,
    )
    await gate.confirm_activation(
        challenge["challenge_id"],
        challenge["challenge_token"],
        "ADMIN_ALICE",
    )
    assert gate.is_live_active() is True

    # Allowed account passes
    allowed, _ = gate.validate_live_order("ICICI_PRIMARY")
    assert allowed is True

    # Non-allowed account fails
    allowed, reason = gate.validate_live_order("UNAUTHORIZED_ACCOUNT_99")
    assert allowed is False
    assert "not in the approved LIVE allowlist" in reason


@pytest.mark.asyncio
async def test_active_window_expiration(clean_gate):
    """Verify gate automatically closes once the activation window expires."""
    gate = clean_gate

    challenge = await gate.request_activation_challenge(
        operator_id="ADMIN_ALICE",
        account_id="ICICI_PRIMARY",
        duration_minutes=10,
    )
    await gate.confirm_activation(
        challenge["challenge_id"],
        challenge["challenge_token"],
        "ADMIN_ALICE",
    )
    assert gate.is_live_active() is True

    # Fast-forward expiry into the past
    gate._expires_at = utc_now() - timedelta(seconds=10)

    assert gate.is_live_active() is False
    allowed, reason = gate.validate_live_order("ICICI_PRIMARY")
    assert allowed is False
    assert "expired" in reason.lower()


@pytest.mark.asyncio
async def test_emergency_revocation(clean_gate):
    """Verify instant manual revocation of LIVE trading mode."""
    gate = clean_gate

    challenge = await gate.request_activation_challenge(
        operator_id="ADMIN_ALICE",
        account_id="ICICI_PRIMARY",
        duration_minutes=60,
    )
    await gate.confirm_activation(
        challenge["challenge_id"],
        challenge["challenge_token"],
        "ADMIN_ALICE",
    )
    assert gate.is_live_active() is True

    # Trigger emergency revocation
    await gate.revoke_live_mode(
        operator_id="RISK_OFFICER",
        reason="Market anomaly detected - aborting live execution",
    )

    assert gate.is_live_active() is False
    status = gate.get_status()
    assert status["live_authorized"] is False
    assert status["time_remaining_sec"] == 0

    allowed, _ = gate.validate_live_order("ICICI_PRIMARY")
    assert allowed is False


@pytest.mark.asyncio
async def test_risk_service_enforces_live_gate(tmp_path):
    """Verify RiskService rejects LIVE orders when gate is closed and approves when open."""
    bus = InMemoryEventBus()
    await bus.start()
    settings = _live_capable_settings(
        data_root=str(tmp_path),
        live_allowed_accounts=["ICICI_PRIMARY"],
    )
    gate = LiveTradingGate(settings=settings, event_bus=bus)
    repo = RiskRepository(db_path=tmp_path / "risk.db")
    portfolio = SimpleNamespace(get_positions=AsyncMock(return_value=[]))
    gateway = SimpleNamespace(
        get_funds=AsyncMock(
            return_value=SimpleNamespace(available_margin=500000.0)
        ),
        get_positions=AsyncMock(return_value=[]),
    )
    risk_svc = RiskService(
        repository=repo,
        event_bus=bus,
        live_gate=gate,
        portfolio_service=portfolio,
        broker_gateway=gateway,
    )
    await risk_svc.initialize()

    live_intent = OrderIntent(
        instrument_id="INST-NIFTY-24800-CE",
        symbol="NIFTY24800CE",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=50,
        price=150.0,
        trading_mode=TradingMode.LIVE,
    )

    # 1. LIVE order rejected by default
    decision = await risk_svc.evaluate_intent(live_intent)
    assert decision.approved is False
    assert decision.rule_name == "LIVE_TRADING_NOT_AUTHORIZED"

    # 2. Activate LIVE gate
    chal = await gate.request_activation_challenge("ADMIN", "ICICI_PRIMARY", 30)
    await gate.confirm_activation(chal["challenge_id"], chal["challenge_token"], "ADMIN")

    # 3. LIVE order is now approved
    decision2 = await risk_svc.evaluate_intent(live_intent)
    assert decision2.approved is True
    assert decision2.rule_name == "ALL_CHECKS_PASSED"

    # 4. Emergency revoke
    await gate.revoke_live_mode("ADMIN", "Testing kill")
    decision3 = await risk_svc.evaluate_intent(live_intent)
    assert decision3.approved is False
    assert decision3.rule_name == "LIVE_TRADING_NOT_AUTHORIZED"

    await risk_svc.stop()
    await bus.stop()


@pytest.mark.asyncio
async def test_api_gateway_live_gate_endpoints(tmp_path):
    """Test full HTTP API Gateway endpoints for Live Trading Gate."""
    test_settings = PlatformSettings(
        data_root=str(tmp_path),
        live_trading_enabled=False,
        live_allowed_accounts=["ICICI_PRIMARY"],
    )
    container = await initialize_services(settings=test_settings)

    import httpx

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. GET status
        resp = await client.get("/api/v1/live-gate/status")
        assert resp.status_code == 200
        assert resp.json()["live_authorized"] is False

        # Authenticate as OPERATOR
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"username": "operator", "password": "Operator@Trading123!"},
        )
        assert login_resp.status_code == 200
        headers = {"Authorization": f"Bearer {login_resp.json()['access_token']}"}

        operator_user_id = login_resp.json()["user"]["user_id"]
        container.live_gate.request_activation_challenge = AsyncMock(
            return_value={
                "challenge_id": "CHAL-BOUND",
                "challenge_token": "CONFIRM-BOUND",
                "expires_in_seconds": 300,
            }
        )
        container.live_gate.confirm_activation = AsyncMock(return_value=True)
        container.live_gate.revoke_live_mode = AsyncMock()

        # 2. Body operator_id is ignored; authenticated identity is authoritative.
        resp = await client.post(
            "/api/v1/live-gate/challenge",
            json={
                "operator_id": "IMPERSONATED-OPERATOR",
                "account_id": "ICICI_PRIMARY",
                "duration_minutes": 45,
            },
            headers=headers,
        )
        assert resp.status_code == 200
        container.live_gate.request_activation_challenge.assert_awaited_once_with(
            operator_id=operator_user_id,
            account_id="ICICI_PRIMARY",
            duration_minutes=45,
        )

        # 3. Confirm is bound to the same authenticated user.
        resp = await client.post(
            "/api/v1/live-gate/confirm",
            json={
                "challenge_id": "CHAL-BOUND",
                "challenge_token": "CONFIRM-BOUND",
                "operator_id": "IMPERSONATED-OPERATOR",
            },
            headers={**headers, "Idempotency-Key": "live-confirm-bound"},
        )
        assert resp.status_code == 200
        container.live_gate.confirm_activation.assert_awaited_once_with(
            challenge_id="CHAL-BOUND",
            challenge_token="CONFIRM-BOUND",
            operator_id=operator_user_id,
        )

        # 4. Revoke also uses authenticated identity, not the request body.
        resp = await client.post(
            "/api/v1/live-gate/revoke",
            json={
                "operator_id": "IMPERSONATED-OPERATOR",
                "reason": "End of trading day",
            },
            headers={**headers, "Idempotency-Key": "live-revoke-bound"},
        )
        assert resp.status_code == 200
        container.live_gate.revoke_live_mode.assert_awaited_once_with(
            operator_id=operator_user_id,
            reason="End of trading day",
        )
