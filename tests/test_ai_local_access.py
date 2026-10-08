"""Local AI API access: no JWT required, but no remote unauthenticated trading."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

import services.api_gateway.ai_local_access as access
import services.api_gateway.ai_routes as routes


class FakeTrades:
    async def list(self, *, limit):
        return [{"trade_id": "paper-001", "status": "OPEN"}]

    async def get(self, trade_id):
        if trade_id == "paper-001":
            return {"trade_id": trade_id, "status": "OPEN"}
        return None

    async def submit(self, *, signal_id, instrument_id, quantity):
        return {
            "trade_id": "paper-002",
            "signal_id": signal_id,
            "instrument_id": instrument_id,
            "quantity": quantity,
            "status": "OPEN",
        }


@pytest.fixture
def application(monkeypatch):
    settings = SimpleNamespace(ai_trust_local_docker_gateway=False)
    container = SimpleNamespace(settings=settings, ai_trade_svc=FakeTrades())
    monkeypatch.setattr(access, "get_services", lambda: container)
    monkeypatch.setattr(routes, "get_services", lambda: container)
    app = FastAPI()
    app.include_router(routes.router)
    return app, settings


@pytest.mark.asyncio
async def test_loopback_can_list_submit_and_monitor_without_a_token(application):
    app, _settings = application
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://localhost:8000"
    ) as client:
        listing = await client.get("/api/v1/ai/trades")
        assert listing.status_code == 200
        assert listing.json()[0]["trade_id"] == "paper-001"

        submit = await client.post(
            "/api/v1/ai/trades",
            json={
                "signal_id": "signal-001",
                "instrument_id": "INST-NIFTY-2026-10-13-22500-CE",
                "quantity": 65,
            },
        )
        assert submit.status_code == 201
        assert submit.json()["trade_id"] == "paper-002"
        assert submit.json()["status"] == "OPEN"

        monitor = await client.get("/api/v1/ai/trades/paper-001")
        assert monitor.status_code == 200
        assert monitor.json()["status"] == "OPEN"

        not_found = await client.get("/api/v1/ai/trades/not-found")
        assert not_found.status_code == 404

        invalid = await client.post(
            "/api/v1/ai/trades",
            json={
                "signal_id": "signal-002",
                "instrument_id": "INST-NIFTY-2026-10-13-22500-CE",
                "quantity": 65,
                "trading_mode": "LIVE",
            },
        )
        assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_remote_peer_denied_even_with_localhost_host_and_spoofed_headers(application):
    app, _settings = application
    transport = httpx.ASGITransport(app=app, client=("203.0.113.10", 12345))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://localhost:8000",
        headers={
            "X-Forwarded-For": "127.0.0.1",
            "X-Real-IP": "127.0.0.1",
            "Authorization": "Bearer not-a-real-token",
        },
    ) as client:
        for path in ("/api/v1/ai/trades", "/api/v1/ai/nifty/snapshot"):
            response = await client.get(path)
            assert response.status_code == 403
            assert "AI_API_LOCAL_ONLY" in response.json()["detail"]
        submit = await client.post(
            "/api/v1/ai/trades",
            json={
                "signal_id": "signal-003",
                "instrument_id": "INST-NIFTY-2026-10-13-22500-CE",
                "quantity": 65,
            },
        )
        assert submit.status_code == 403


@pytest.mark.asyncio
async def test_exact_docker_gateway_requires_operator_opt_in_and_localhost_host(
    application, monkeypatch
):
    app, settings = application
    monkeypatch.setattr(access, "_docker_default_gateway", lambda: "172.19.0.1")
    transport = httpx.ASGITransport(app=app, client=("172.19.0.1", 55000))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://localhost:8000"
    ) as client:
        assert (await client.get("/api/v1/ai/trades")).status_code == 403

        settings.ai_trust_local_docker_gateway = True
        assert (await client.get("/api/v1/ai/trades")).status_code == 200

        # Host header alone can never bypass the source-peer check.
        assert (
            await client.get("/api/v1/ai/trades", headers={"Host": "evil.example"})
        ).status_code == 403

    other_peer = httpx.ASGITransport(app=app, client=("172.19.0.5", 55001))
    async with httpx.AsyncClient(
        transport=other_peer, base_url="http://localhost:8000"
    ) as client:
        assert (await client.get("/api/v1/ai/trades")).status_code == 403
