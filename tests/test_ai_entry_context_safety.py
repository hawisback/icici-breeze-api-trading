"""Fail-closed AI entry contracts, independent of a live broker session."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from libs.contracts.models import Candle, Quote, SystemMode, utc_now
from services.ai_context.features import compute_technicals
from services.ai_context.service import AIContextService
from services.api_gateway.ai_routes import router
from services.api_gateway.dependencies import get_current_user


def _candle(end, *, source="KITE", interval="1m", close=22500.0):
    minutes = {"1m": 1, "5m": 5, "15m": 15}[interval]
    return Candle(
        instrument_id="INST-NIFTY-INDEX",
        interval=interval,
        start_time=end - timedelta(minutes=minutes),
        end_time=end,
        open=close - 1,
        high=close + 2,
        low=close - 2,
        close=close,
        volume=2500,
        source=source,
    )


def _service(**kwargs):
    defaults = dict(
        market_data_service=None,
        historical_service=None,
        option_chain_service=None,
        portfolio_service=None,
        broker_gateway=None,
        broker_session_service=None,
        risk_service=None,
        order_management_service=None,
    )
    defaults.update(kwargs)
    return AIContextService(**defaults)


def test_ai_routes_require_server_identity():
    assert any(dependency.dependency is get_current_user for dependency in router.dependencies)
    assert all(route.methods == {"GET"} for route in router.routes if route.path != "/api/v1/ai/trades")
    assert any(route.methods == {"POST"} for route in router.routes)


def test_only_completed_real_valid_candles_are_exposed():
    now = utc_now().replace(second=0, microsecond=0)
    real = _candle(now - timedelta(minutes=1))
    synthetic = _candle(now - timedelta(minutes=2), source="SIMULATED")
    future = _candle(now + timedelta(minutes=2))
    invalid = _candle(now - timedelta(minutes=3)).model_copy(update={"low": 23000.0})
    result = AIContextService._completed_real_candles(
        [real, synthetic, future, invalid], "1m"
    )
    assert result == [real]


def test_indicators_expose_preceding_completed_bar_evidence():
    start = utc_now().replace(second=0, microsecond=0) - timedelta(minutes=80)
    bars = [
        _candle(start + timedelta(minutes=i + 1), close=22500 + (i * 1.5))
        for i in range(70)
    ]
    metrics = compute_technicals(bars)
    assert metrics["macd_histogram_prev"] is not None
    assert metrics["macd_histogram_prev2"] is not None
    assert metrics["rsi_14_prev"] is not None
    assert metrics["bar_end_time"] == bars[-1].end_time.isoformat()
    assert isinstance(metrics["macd_crossed_above_zero"], bool)
    assert isinstance(metrics["rsi_crossed_above_55"], bool)


@pytest.mark.asyncio
async def test_option_context_flags_missing_atm_right_as_incomplete():
    now = utc_now().isoformat()
    strike = 22500
    rows = []
    for offset in range(-10, 11):
        value = strike + offset * 50
        def contract(right):
            return {
                "ltp": 100, "bid": 99, "ask": 100,
                "open_interest": 1000, "volume": 100,
                "market_timestamp": now, "symbol": f"NIFTY{value}{right}"
            }
        rows.append({
            "strike": value,
            "call": contract("CE"),
            "put": None if offset == 0 else contract("PE"),
        })

    class Chain:
        async def get_chain(self, **kwargs):
            assert kwargs["provider"] == "kite"
            return {
                "underlying": "NIFTY", "source": "KITE",
                "atm_strike": strike, "expiry": "2026-10-13",
                "captured_at": now, "strikes": rows,
            }

    result = await _service(option_chain_service=Chain()).get_options(strike_window=10)
    assert result["available"] is True
    assert result["pcr_scope"] == "ATM_PLUS_MINUS_10_STRIKES"
    assert result["quote_coverage_ratio"] < 1.0
    assert result["atm_quote_valid"] is False
    assert result["market_data_age_seconds"] is not None


@pytest.mark.asyncio
async def test_unconfirmed_order_blocks_an_accurate_flat_account():
    class Portfolio:
        async def get_positions(self):
            return []
        async def get_pnl_summary(self):
            return {"total_pnl": 0}

    class Broker:
        async def get_positions(self, mode):
            return []
        async def get_funds(self, mode):
            return {"available": 50000}

    class Session:
        async def get_all_session_statuses(self):
            return []
        async def get_session_status(self):
            return {"connected": True}

    class Risk:
        async def get_system_mode(self):
            return SystemMode.NORMAL

    class Oms:
        async def list_orders(self, limit):
            return [SimpleNamespace(status="SUBMISSION_UNKNOWN", order_id="unconfirmed")]

    svc = _service(
        portfolio_service=Portfolio(), broker_gateway=Broker(),
        broker_session_service=Session(), risk_service=Risk(),
        order_management_service=Oms(),
    )
    result = await svc.get_account_context()
    assert result["local_open_positions_count"] == 0
    assert result["broker_open_positions_count"] == 0
    assert result["entry_context_ready"] is False
    assert "PENDING_OR_UNKNOWN_ORDERS" in result["entry_blockers"]


@pytest.mark.asyncio
async def test_snapshot_data_readiness_ignores_other_process_account_blockers():
    quote = Quote(
        instrument_id="INST-NIFTY-INDEX", symbol="NIFTY 50",
        source="KITE", last_price=22500.0, timestamp=utc_now(),
    )
    svc = _service(market_data_service=SimpleNamespace(get_latest_quote=lambda _: quote))

    async def get_technicals(*, interval, limit):
        return {
            "available": True, "age_seconds": 30.0,
            "missing_recent_session_bars": 0,
            "metrics": {
                "data_points": 70, "macd_histogram_prev": -0.1,
                "rsi_14_prev": 50,
            },
        }

    async def get_options(*, strike_window):
        assert strike_window == 10
        return {
            "available": True, "expiry": "2026-10-13",
            "market_data_age_seconds": 5.0,
            "market_timestamp": utc_now().isoformat(),
            "quote_timestamps_complete": True, "quote_coverage_ratio": 1.0,
            "atm_quote_valid": True, "atm_max_spread_pct": 1.0,
            "summary": {"pcr_oi": 1.0},
        }

    async def get_data_quality():
        return {"quote_ready": True}

    async def get_account_context():
        return {
            "entry_context_ready": False,
            "entry_blockers": ["SYSTEM_MODE_NOT_NORMAL", "PENDING_OR_UNKNOWN_ORDERS"],
            "local_open_positions_count": 0,
            "broker_open_positions_count": None,
            "local_portfolio": {"positions": [], "pnl": None},
        }

    svc.get_technicals = get_technicals
    svc.get_options = get_options
    svc.get_data_quality = get_data_quality
    svc.get_account_context = get_account_context
    svc._session_context = lambda expiry: {"regular_session": True}
    result = await svc.get_snapshot()
    assert result["entry_data_ready"] is True
    # Strategy/OMS risk states are returned as evidence, never as an AI entry veto.
    assert result["entry_permitted"] is True
    assert result["blocking_reasons"] == []
    assert result["account"]["entry_context_ready"] is False
    assert result["account"]["entry_blockers"] == ["SYSTEM_MODE_NOT_NORMAL", "PENDING_OR_UNKNOWN_ORDERS"]
    assert result["readiness_scope"] == "MARKET_DATA_ONLY"
    assert result["schema_version"] == "1.2"


@pytest.mark.asyncio
async def test_risk_rejected_order_is_terminal_not_a_pending_order():
    class Portfolio:
        async def get_positions(self):
            return []
        async def get_pnl_summary(self):
            return {"total_pnl": 0}

    class Broker:
        async def get_positions(self, mode):
            return []
        async def get_funds(self, mode):
            return {"available": 50000}

    class Session:
        async def get_all_session_statuses(self):
            return []
        async def get_session_status(self):
            return {"connected": True}

    class Risk:
        async def get_system_mode(self):
            return SystemMode.ENTRY_BLOCKED

    class Oms:
        async def list_orders(self, limit):
            return [SimpleNamespace(status="RISK_REJECTED", order_id="rejected-001")]

    svc = _service(
        portfolio_service=Portfolio(), broker_gateway=Broker(),
        broker_session_service=Session(), risk_service=Risk(),
        order_management_service=Oms(),
    )
    account = await svc.get_account_context()
    assert account["order_book"]["pending_count"] == 0
    assert "PENDING_OR_UNKNOWN_ORDERS" not in account["entry_blockers"]
    assert account["risk_system_mode"] == "ENTRY_BLOCKED"
    assert "SYSTEM_MODE_NOT_NORMAL" in account["entry_blockers"]
