"""Integration tests for Strategy REST API endpoints in API Gateway.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from services.api_gateway.main import app
from services.api_gateway.service_container import initialize_services


@pytest.mark.asyncio
async def test_strategy_api_endpoints():
    container = await initialize_services()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. GET /api/v1/strategies/status
        res = await client.get("/api/v1/strategies/status")
        assert res.status_code == 200
        data = res.json()
        assert "config" in data
        assert "features" in data
        assert "active_trades" in data
        assert "strategies" in data

        # 2. GET /api/v1/strategies/config
        res = await client.get("/api/v1/strategies/config")
        assert res.status_code == 200
        cfg = res.json()
        assert "option_selection" in cfg and "max_option_premium" in cfg["option_selection"]

        # 3. POST /api/v1/strategies/config
        cfg["option_selection"]["max_option_premium"] = 65.0
        res = await client.post("/api/v1/strategies/config", json=cfg)
        assert res.status_code == 200
        assert res.json()["config"]["option_selection"]["max_option_premium"] == 65.0

        # 4. POST /api/v1/strategies/arm
        res = await client.post("/api/v1/strategies/arm", json={"armed": True})
        assert res.status_code == 200
        assert res.json()["config"]["system_armed"] is True

        res = await client.post("/api/v1/strategies/arm", json={"armed": False})
        assert res.status_code == 200
        assert res.json()["config"]["system_armed"] is False

        # 5. POST /api/v1/strategies/auto-trade
        res = await client.post("/api/v1/strategies/auto-trade", json={"enabled": True})
        assert res.status_code == 200
        assert res.json()["config"]["auto_trade_enabled"] is True

        # 6. POST /api/v1/strategies/evaluate-now
        res = await client.post("/api/v1/strategies/evaluate-now")
        assert res.status_code == 200
        assert res.json()["status"] == "SUCCESS"

        # 7. GET /api/v1/strategies/decision-log
        res = await client.get("/api/v1/strategies/decision-log?limit=10")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

        # 8. GET /api/v1/strategies/trades
        res = await client.get("/api/v1/strategies/trades?limit=10")
        assert res.status_code == 200
        assert isinstance(res.json(), list)

        # 9. GET /api/v1/strategies/triggers/diagnostics
        res = await client.get("/api/v1/strategies/triggers/diagnostics")
        assert res.status_code == 200
        diag = res.json()
        assert "gates" in diag
        assert "strategies" in diag
        assert "active_overrides" in diag
        assert len(diag["strategies"]) == 4  # Bullish & Bearish for Strategy A and B

        # 10. GET & POST /api/v1/strategies/overrides
        res = await client.get("/api/v1/strategies/overrides")
        assert res.status_code == 200

        res = await client.post(
            "/api/v1/strategies/overrides",
            json={
                "max_option_premium_cap": 140.0,
                "adx_threshold": 15.0,
                "rvol_threshold": 1.1,
                "bypass_entry_window": True,
            },
        )
        assert res.status_code == 200
        assert res.json()["overrides"]["max_option_premium_cap"] == 140.0
        assert res.json()["overrides"]["bypass_entry_window"] is True

        # 11. POST /api/v1/strategies/force-entry
        res = await client.post(
            "/api/v1/strategies/force-entry",
            json={
                "strategy": "TREND_PULLBACK",
                "direction": "BULLISH",
                "option_type": "CALL",
                "override_premium_cap": 250.0,
            },
        )
        assert res.status_code == 200
        force_res = res.json()
        assert force_res["status"] == "TRADE_OPENED"
        assert "trade" in force_res
        assert force_res["trade"]["direction"] == "BULLISH"
        assert force_res["trade"]["option_type"] == "CALL"

        # 12. POST /api/v1/strategies/overrides/reset
        res = await client.post("/api/v1/strategies/overrides/reset")
        assert res.status_code == 200
        assert res.json()["overrides"]["bypass_entry_window"] is False

    # Teardown
    await container.strategy_svc.stop()



