"""Tests verifying API Gateway REST endpoints and WebSocket behavior."""

import pytest
import httpx
from services.api_gateway.main import app
from services.api_gateway.service_container import initialize_services


@pytest.mark.asyncio
async def test_api_gateway_health_and_endpoints():
    """Verify system health, market quotes, and option chain endpoints via ASGI."""
    await initialize_services()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. System health
        res = await client.get("/api/v1/system/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "HEALTHY"
        assert "services" in data

        # 2. Account funds
        res = await client.get("/api/v1/account/funds")
        assert res.status_code == 200
        funds = res.json()
        assert "available_margin" in funds

        # 3. Market quotes
        res = await client.get("/api/v1/market/quotes")
        assert res.status_code == 200
        quotes = res.json()
        assert len(quotes) >= 2

        # 4. Expiries
        res = await client.get("/api/v1/instruments/expiries?underlying=NIFTY")
        assert res.status_code == 200
        assert len(res.json()["expiries"]) > 0

        # 5. Option chain
        res = await client.get("/api/v1/options/chain?underlying=NIFTY")
        assert res.status_code == 200
        chain = res.json()
        assert "strikes" in chain
        assert len(chain["strikes"]) > 0

        # 6. Orders list
        res = await client.get("/api/v1/orders")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

        # 7. Post order (requires TRADER authentication)
        login_res = await client.post(
            "/api/v1/auth/login",
            json={"username": "trader", "password": "Trader@Trading123!"},
        )
        assert login_res.status_code == 200
        token = login_res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        order_payload = {
            "instrument_id": "INST-NIFTY-2026-09-24-24800-CE",
            "symbol": "NIFTY24800CE",
            "side": "BUY",
            "order_type": "LIMIT",
            "quantity": 25,
            "price": 120.0,
            "trading_mode": "PAPER",
        }
        res = await client.post("/api/v1/orders", json=order_payload, headers=headers)
        assert res.status_code == 200
        created = res.json()
        assert created["symbol"] == "NIFTY24800CE"
        assert created["client_order_id"].startswith("CL-")
