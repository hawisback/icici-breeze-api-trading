"""Forward option execution validation tests.

These tests exercise only capture/execution plumbing.  They do not alter or
replay Strategy A signal rules.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from services.strategy.models import (
    ActiveTrade,
    AutoTradingMode,
    MarketFeatures,
    OptionType,
    StrategyName,
    TradeDirection,
    TradeLifecycleState,
)
from services.strategy.service import StrategyService


def _trade(mode=AutoTradingMode.PAPER, option_type=OptionType.PUT):
    return ActiveTrade(
        trade_id="TRD-FORWARD-1",
        mode=mode,
        strategy=StrategyName.TREND_PULLBACK,
        direction=TradeDirection.BEARISH if option_type == OptionType.PUT else TradeDirection.BULLISH,
        option_type=option_type,
        contract_symbol="NIFTY-24000-PE" if option_type == OptionType.PUT else "NIFTY-24000-CE",
        contract_instrument_id="OPT-1",
        expiry="2026-09-24",
        strike=24000,
        quantity=50,
        lot_size=50,
        lots=1,
        entry_option_price=100,
        entry_spot_price=24000,
        initial_structural_stop=24100,
        initial_r_points=100,
        current_option_price=100,
        current_spot_price=24000,
        current_trailing_stop=24100,
        option_hard_stop_price=75,
        selected_contract_snapshot={
            "expiry": "2026-09-24", "strike": 24000, "option_type": option_type.value,
            "instrument_id": "OPT-1", "symbol": "NIFTY-24000-PE",
        },
        entry_raw_ask=100,
        entry_executable_price=101,
        entry_slippage_points=1,
        signal_id="SIG-1",
    )


def _service(quote, slippage=1.0):
    repo = Mock(
        save_signal=AsyncMock(),
        save_trade=AsyncMock(),
        save_runtime=AsyncMock(),
        save_decision_log=AsyncMock(),
        save_option_quote=AsyncMock(),
        save_execution_ledger=AsyncMock(),
    )
    oms = Mock(create_order_intent=AsyncMock())
    market = Mock(get_latest_quote=Mock(return_value=quote))
    service = StrategyService(oms, repository=repo, market_data_service=market, event_bus=Mock(publish=AsyncMock()))
    service.config.risk.paper_slippage_points = slippage
    return service, repo, oms


@pytest.mark.asyncio
async def test_call_shadow_and_put_paper_never_submit_broker_order():
    service, _, oms = _service(None)
    await service.emit_signal(
        "INST", "NIFTY-CE", "OPT-1", side="BUY", suggested_price=100,
        quantity=50, trading_mode="PAPER", metadata={"strategy": "TREND_PULLBACK"},
    )
    oms.create_order_intent.assert_not_awaited()


@pytest.mark.asyncio
async def test_actual_ltp_is_supplied_to_position_manager_and_option_hard_stop_closes():
    quote = SimpleNamespace(
        source="BREEZE", instrument_id="OPT-1", symbol="NIFTY-24000-PE",
        last_price=74, best_bid=73, best_ask=75, volume=1000, open_interest=50000,
        timestamp=datetime.now(timezone.utc),
    )
    service, repo, oms = _service(quote, slippage=1)
    trade = _trade()
    service.position_manager.update_position = Mock(return_value=(trade, "OPTION_HARD_STOP_HIT"))
    await service._evaluate_active_trade(trade, MarketFeatures(spot_price=24000))
    service.position_manager.update_position.assert_called_once()
    assert service.position_manager.update_position.call_args.args[1] == 74
    assert trade.option_exit_reason == "OPTION_EMERGENCY_STOP"
    assert trade.underlying_exit_reason == "OPTION_HARD_STOP_PREEMPTED_UNDERLYING"
    oms.create_order_intent.assert_not_awaited()
    repo.save_execution_ledger.assert_awaited()


@pytest.mark.asyncio
async def test_entry_and_exit_use_ask_and_bid_with_configured_slippage():
    quote = SimpleNamespace(
        source="BREEZE", instrument_id="OPT-1", symbol="NIFTY-24000-PE",
        last_price=108, best_bid=107, best_ask=110, volume=1000, open_interest=50000,
        timestamp=datetime.now(timezone.utc),
    )
    service, _, _ = _service(quote, slippage=2)
    trade = _trade()
    service.position_manager.update_position = Mock(return_value=(trade, "UNDERLYING_EXIT"))
    await service._evaluate_active_trade(trade, MarketFeatures(spot_price=24000))
    assert trade.exit_option_price == 105  # bid 107 minus two points
    assert trade.option_exit_reason == "UNDERLYING_EXIT"


@pytest.mark.asyncio
async def test_missing_quote_does_not_create_synthetic_fill_but_manages_strategy_a_underlying():
    service, repo, _ = _service(None)
    trade = _trade()
    service.position_manager.update_position = Mock(return_value=(trade, None))
    await service._evaluate_active_trade(trade, MarketFeatures(spot_price=24000))
    service.position_manager.update_position.assert_called_once()
    assert service.position_manager.update_position.call_args.args[1] is None
    assert trade.state != TradeLifecycleState.CLOSED
    assert trade.option_data_status == "UNAVAILABLE"
    repo.save_trade.assert_awaited()


def test_selected_contract_identity_is_immutable_during_quote_updates():
    service, _, _ = _service(None)
    trade = _trade()
    identity = dict(trade.selected_contract_snapshot)
    service._apply_quote_to_trade(trade, {
        "status": "VALID", "source": "BREEZE", "bid": 91, "ask": 92,
        "ltp": 91.5, "volume": 100, "open_interest": 1000,
        "quote_timestamp": datetime.now(timezone.utc).isoformat(), "freshness_seconds": 1,
    })
    assert trade.selected_contract_snapshot == identity
    assert trade.contract_instrument_id == "OPT-1"
