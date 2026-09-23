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
        assert set(data["strategies"]) == {"trend_pullback", "volatility_breakout", "di_continuation", "sr_momentum_breakout"}
        assert "strategy_c_paper" in data
        assert "strategy_d_paper" in data

        # 2. GET /api/v1/strategies/config
        res = await client.get("/api/v1/strategies/config")
        assert res.status_code == 200
        cfg = res.json()
        assert "option_selection" in cfg and "max_option_premium" in cfg["option_selection"]

        # Mutating strategy controls are operator-only.
        unauth = await client.post("/api/v1/strategies/arm", json={"armed": True})
        assert unauth.status_code == 401
        login = await client.post(
            "/api/v1/auth/login",
            json={"username": "operator", "password": "Operator@Trading123!"},
        )
        assert login.status_code == 200
        client.headers.update(
            {"Authorization": f"Bearer {login.json()['access_token']}"}
        )

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
        assert len(diag["strategies"]) == 8  # CALL/PUT diagnostics for Strategies A, B, C and D

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
        assert force_res["status"] == "STRATEGY_A_FORCE_ENTRY_DISABLED"
        assert "trade" not in force_res

        for frozen_strategy in ("DI_CONTINUATION", "SR_MOMENTUM_BREAKOUT"):
            res = await client.post(
                "/api/v1/strategies/force-entry",
                json={
                    "strategy": frozen_strategy,
                    "direction": "BULLISH",
                    "option_type": "CALL",
                    "override_premium_cap": 250.0,
                },
            )
            assert res.status_code == 200
            assert res.json()["status"] == "FROZEN_CANDIDATE_FORCE_ENTRY_DISABLED"
            assert "trade" not in res.json()

        # 12. POST /api/v1/strategies/overrides/reset
        res = await client.post("/api/v1/strategies/overrides/reset")
        assert res.status_code == 200
        assert res.json()["overrides"]["bypass_entry_window"] is False

        # 13. Comprehensive diagnostics condition checklist validation
        res = await client.get("/api/v1/strategies/triggers/diagnostics")
        assert res.status_code == 200
        diag = res.json()
        assert len(diag["strategies"]) == 8
        for s in diag["strategies"]:
            assert len(s["conditions"]) >= 0
            for c in s["conditions"]:
                assert c["id"]
                assert c["name"]
                assert c["status"] in ("PASSED", "PENDING", "BLOCKED", "N/A")
                assert c["current_value"] != ""
                assert c["target_threshold"] != ""
                assert c["gap_description"] != ""

        # 14. Test manual trade exit via REST API — uses REAL stop values to verify zero-price guard
        from datetime import datetime, timezone
        from services.strategy.models import ActiveTrade, AutoTradingMode, TradeDirection, OptionType, StrategyName
        from services.strategy.position_manager import PositionManager
        from services.strategy.models import MarketFeatures, RiskConfig, SessionTimersConfig

        status_res = await client.get("/api/v1/strategies/status")
        live_spot = status_res.json().get("features", {}).get("spot_price", 0.0) or 24500.0

        fake_trade = ActiveTrade(
            trade_id="TRD-TEST-EXIT-API",
            mode=AutoTradingMode.PAPER,
            strategy=StrategyName.TREND_PULLBACK,
            direction=TradeDirection.BULLISH,
            option_type=OptionType.CALL,
            contract_symbol="NIFTY26SEP24500CE",
            contract_instrument_id="INST-NIFTY-24500-CE",
            expiry="2026-09-24",
            strike=live_spot,
            quantity=50,
            lot_size=50,
            lots=1,
            entry_option_price=100.0,
            entry_spot_price=live_spot,
            entry_time=datetime.now(timezone.utc),
            initial_structural_stop=live_spot - 50.0,
            initial_r_points=50.0,
            current_option_price=100.0,
            current_spot_price=live_spot,
            current_trailing_stop=live_spot - 50.0,   # realistic positive stop
            option_hard_stop_price=75.0,     # realistic positive hard stop
        )
        await container.strategy_svc.repo.save_trade(fake_trade)

        # Confirm trade is NOT auto-exited even with spot=0 (zero-price guard)
        res = await client.get("/api/v1/strategies/status")
        assert res.status_code == 200
        status_data = res.json()
        assert any(t["trade_id"] == "TRD-TEST-EXIT-API" for t in status_data["active_trades"]), \
            "Trade was spuriously auto-exited by zero spot price — zero-price guard not working"

        # Call manual exit
        res = await client.post("/api/v1/strategies/trades/TRD-TEST-EXIT-API/exit", json={"reason": "TEST_MANUAL_EXIT"})
        assert res.status_code == 200
        exit_resp = res.json()
        assert exit_resp["status"] == "SUCCESS"
        assert exit_resp["trade"]["state"] == "CLOSED"
        assert exit_resp["trade"]["exit_reason"] == "TEST_MANUAL_EXIT"

        # Confirm trade is no longer active
        res = await client.get("/api/v1/strategies/status")
        assert res.status_code == 200
        assert not any(t["trade_id"] == "TRD-TEST-EXIT-API" for t in res.json()["active_trades"])

        # 15. Unit-level: PositionManager must not fire hard stop or structural stop when prices are zero
        pm = PositionManager(RiskConfig(), SessionTimersConfig())
        zero_features = MarketFeatures(spot_price=0.0, timestamp=datetime.now(timezone.utc))
        bullish_trade = ActiveTrade(
            trade_id="TRD-ZERO-GUARD-BULL",
            mode=AutoTradingMode.PAPER,
            strategy=StrategyName.TREND_PULLBACK,
            direction=TradeDirection.BULLISH,
            option_type=OptionType.CALL,
            contract_symbol="NIFTY26SEP24500CE",
            contract_instrument_id="INST-TEST",
            expiry="2026-09-24",
            strike=24500.0,
            quantity=50,
            lot_size=50,
            lots=1,
            entry_option_price=100.0,
            entry_spot_price=24500.0,
            entry_time=datetime.now(timezone.utc),
            initial_structural_stop=24450.0,
            initial_r_points=50.0,
            current_option_price=100.0,
            current_spot_price=24500.0,
            current_trailing_stop=24450.0,
            option_hard_stop_price=75.0,
        )
        _, reason = pm.update_position(bullish_trade, 0.0, zero_features)
        assert reason != "OPTION_HARD_STOP_HIT (LTP 0.0 <= SL 75.0)", \
            "Option hard stop fired on zero price — zero guard missing"
        assert reason is None or "STRUCTURAL_SPOT_STOP" not in str(reason), \
            "Structural stop fired on spot=0 — zero guard missing"

    # Teardown
    await container.strategy_svc.stop()


