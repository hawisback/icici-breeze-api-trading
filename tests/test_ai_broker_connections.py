"""Local daily Kite/Breeze login is available without platform-user JWT.

The broker's official one-time callback state is reused. No request token,
API secret or exchanged access token is returned to the dashboard.
"""

from __future__ import annotations

from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

import services.api_gateway.ai_local_access as local_access
import services.api_gateway.ai_routes as ai_routes
from services.broker_session.service import BrokerSessionService


class FakeSessions:
    def __init__(self):
        self.challenge = None
        self.calls = []
        self.connected = {"kite": False, "breeze": False}

    async def get_all_session_statuses(self):
        return {
            broker: {
                "status": "CONNECTED" if self.connected[broker] else "DISCONNECTED",
                "connected": self.connected[broker],
                "expires_at": "2026-10-09T15:00:00+00:00" if self.connected[broker] else None,
            }
            for broker in ("kite", "breeze")
        }

    def issue_login_challenge(self, *, initiated_by, broker_backend):
        self.challenge = {"initiated_by": initiated_by, "broker": broker_backend}
        return {"state": "a" * 35, "expires_at": "2026-10-09T09:00:00+00:00"}

    async def activate_session(self, *, api_key, secret_key, session_token, account_id, broker_backend):
        self.calls.append({
            "api_key": api_key, "secret_key": secret_key,
            "session_token": session_token, "account_id": account_id,
            "broker_backend": broker_backend,
        })
        if session_token == "invalid-token":
            return {"status": "AUTHENTICATION_FAILED", "connected": False}
        self.connected[broker_backend] = True
        return {"status": "CONNECTED", "connected": True}


@pytest.fixture
def local_broker_api(monkeypatch):
    statuses = FakeSessions()
    settings = SimpleNamespace(
        ai_trust_local_docker_gateway=False,
        ai_trade_mode=SimpleNamespace(value="PAPER"),
        kite_api_key=SecretStr("kite-test-key"),
        kite_api_secret=SecretStr("kite-test-secret"),
        breeze_api_key=SecretStr("breeze-test-key"),
        breeze_secret_key=SecretStr("breeze-test-secret"),
    )
    kite_adapter = SimpleNamespace(access_token="kite-exchanged-access-token")
    container = SimpleNamespace(
        settings=settings,
        session_svc=statuses,
        gateway_svc=SimpleNamespace(kite_adapter=kite_adapter),
    )
    persist = Mock(return_value=True)
    monkeypatch.setattr(ai_routes, "get_services", lambda: container)
    monkeypatch.setattr(local_access, "get_services", lambda: container)
    monkeypatch.setattr(ai_routes, "update_env_variable", persist)
    app = FastAPI()
    app.include_router(ai_routes.router)
    return app, statuses, settings, persist


@pytest.mark.asyncio
async def test_both_local_brokers_have_login_button_and_no_token_response(local_broker_api):
    app, sessions, _, persist = local_broker_api
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 9898))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
        status = await client.get("/api/v1/ai/broker/sessions")
        assert status.status_code == 200
        data = status.json()
        assert data["market_data_broker"] == "kite"
        assert data["trading_mode"] == "PAPER"
        assert data["kite"]["configured"] is True
        assert data["breeze"]["connected"] is False
        assert "token" not in str(data).lower()
        assert "secret" not in str(data).lower()

        for broker in ("kite", "breeze"):
            res = await client.post(
                "/api/v1/ai/broker/session/login-url",
                json={"broker": broker},
            )
            assert res.status_code == 200
            url = urlparse(res.json()["login_url"])
            assert url.hostname == "127.0.0.1"
            assert url.path == "/api/v1/broker/session/start"
            assert parse_qs(url.query)["broker"] == [broker]
            assert parse_qs(url.query)["state"] == ["a" * 35]
            assert sessions.challenge == {
                "initiated_by": "LOCAL_AI_DASHBOARD",
                "broker": broker,
            }
        persist.assert_not_called()


@pytest.mark.asyncio
async def test_manual_kite_and_breeze_activation_persist_only_successful_sessions(local_broker_api):
    app, sessions, _, persist = local_broker_api
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 9898))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
        failure = await client.post(
            "/api/v1/ai/broker/session/activate",
            json={"broker": "kite", "token": "invalid-token"},
        )
        assert failure.status_code == 400
        persist.assert_not_called()

        kite = await client.post(
            "/api/v1/ai/broker/session/activate",
            json={"broker": "kite", "token": "one-time-kite-request-token"},
        )
        assert kite.status_code == 200
        assert kite.json()["connected"] is True
        assert "one-time-kite-request-token" not in str(kite.json())
        assert "kite-exchanged-access-token" not in str(kite.json())
        persist.assert_any_call(key="KITE_ACCESS_TOKEN", value="kite-exchanged-access-token")
        assert sessions.calls[-1]["account_id"] == "ZERODHA_PRIMARY"

        breeze = await client.post(
            "/api/v1/ai/broker/session/activate",
            json={"broker": "breeze", "token": "breeze-daily-apisession"},
        )
        assert breeze.status_code == 200
        assert breeze.json()["connected"] is True
        assert "breeze-daily-apisession" not in str(breeze.json())
        persist.assert_any_call(key="BREEZE_SESSION_TOKEN", value="breeze-daily-apisession")
        assert sessions.calls[-1]["account_id"] == "ICICI_PRIMARY"

        statuses = (await client.get("/api/v1/ai/broker/sessions")).json()
        assert statuses["kite"]["connected"] is True
        assert statuses["breeze"]["connected"] is True


@pytest.mark.asyncio
async def test_missing_api_keys_and_wrong_source_cannot_issue_login(local_broker_api):
    app, sessions, settings, persist = local_broker_api
    transport = httpx.ASGITransport(app=app, client=("198.51.100.12", 9898))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
        assert (await client.get("/api/v1/ai/broker/sessions")).status_code == 403
        assert (await client.post(
            "/api/v1/ai/broker/session/login-url",
            json={"broker": "kite"},
        )).status_code == 403
        assert (await client.post(
            "/api/v1/ai/broker/session/activate",
            json={"broker": "breeze", "token": "some-token"},
        )).status_code == 403
    assert sessions.calls == []
    assert persist.call_count == 0

    settings.kite_api_secret = None
    local = httpx.ASGITransport(app=app, client=("127.0.0.1", 9898))
    async with httpx.AsyncClient(transport=local, base_url="http://localhost:8000") as client:
        res = await client.post("/api/v1/ai/broker/session/login-url", json={"broker": "kite"})
        assert res.status_code == 400
        assert "BROKER_CREDENTIALS_NOT_CONFIGURED" in res.json()["detail"]
        bad = await client.post("/api/v1/ai/broker/session/login-url", json={"broker": "invalid"})
        assert bad.status_code == 422


def test_broker_dashboard_connect_is_not_gated_by_status_or_key_configuration():
    """A failed status fetch must not leave Connect/Activate permanently disabled."""
    from pathlib import Path

    project = Path(__file__).resolve().parents[1]
    source = (
        project / "frontend/trading-ui/features/broker_connections/BrokerConnections.tsx"
    ).read_text(encoding="utf-8")
    assert "disabled={!ready" not in source
    assert "disabled={submitting !== null || !ready}" not in source
    assert "disabled={connecting === broker || submitting !== null}" in source
    assert "KITE_API_KEY and KITE_API_SECRET" in source
    assert "BREEZE_API_KEY and BREEZE_SECRET_KEY" in source
    assert "restart Uvicorn" in source


def test_next_layout_ignores_only_extension_level_hydration_attributes():
    from pathlib import Path

    project = Path(__file__).resolve().parents[1]
    layout = (
        project / "frontend/trading-ui/app/layout.tsx"
    ).read_text(encoding="utf-8")
    # Browser extensions inject data-* attributes into html/body before the
    # client hydrates. Next suppressHydrationWarning applies one level only.
    assert 'className="dark h-full" suppressHydrationWarning' in layout
    assert 'font-sans" suppressHydrationWarning' in layout
