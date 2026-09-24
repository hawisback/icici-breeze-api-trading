from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from libs.contracts.models import OrderSide, TradingMode
from services.strategy.models import (
    AutoTradingMode,
    MarketFeatures,
    OptionType,
    SelectedContract,
    StrategyName,
    StrategySignal,
    TradeDirection,
)
from services.strategy.repository import StrategyRepository
from services.strategy.service import StrategyService


def _signal() -> StrategySignal:
    return StrategySignal(
        signal_id="SIG-B-FORWARD-1",
        strategy=StrategyName.VOLATILITY_BREAKOUT,
        direction=TradeDirection.BULLISH,
        option_type=OptionType.CALL,
        timestamp=datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc),
        spot_reference_price=25000.0,
        structural_stop=24900.0,
        r_points=100.0,
        derivatives_score=3.0,
    )


def _service():
    repo = Mock(
        save_signal=AsyncMock(),
        save_option_chain_snapshot=AsyncMock(),
        save_decision_log=AsyncMock(),
        save_auto_config=AsyncMock(),
        get_runtime=AsyncMock(return_value=None),
        save_runtime=AsyncMock(),
    )
    oms = Mock(create_order_intent=AsyncMock())
    service = StrategyService(oms, repository=repo, event_bus=Mock(publish=AsyncMock()))
    service._market_data_status.update({
        "execution_feed_healthy": True,
        "execution_feed_reasons": [],
        "strategy_a_signal_data_fresh": True,
        "strategy_b_signal_data_fresh": True,
    })
    return service, repo, oms


def _selected_contract() -> SelectedContract:
    return SelectedContract(
        instrument_id="OPT-B-1", symbol="NIFTY 25000 CALL", expiry="2026-09-24",
        strike=25000, option_type=OptionType.CALL, ask_price=52, bid_price=51,
        open_interest=50000, volume=1200, spread_pct=1.94, lot_size=50,
        ltp=51.5, instrument_token="TOKEN-B-1", premium=52,
    )


def test_strategy_b_global_live_resolves_live_after_promotion():
    service, _, _ = _service()
    service.config.mode = AutoTradingMode.LIVE
    assert service._execution_mode_for_signal(_signal()) == AutoTradingMode.LIVE
    policy = service._execution_policy_for_strategy(
        StrategyName.VOLATILITY_BREAKOUT
    )
    assert policy.live_trading_allowed is True
    assert policy.promotion_state == "LIVE_PROMOTED"


@pytest.mark.asyncio
async def test_strategy_b_live_compat_signal_never_dispatches_to_oms():
    service, _, oms = _service()
    await service.emit_signal(
        "INST", "NIFTY-25000-CE", "OPT-B-1", side=OrderSide.BUY,
        suggested_price=50, quantity=50, trading_mode=TradingMode.LIVE,
        metadata={"strategy": StrategyName.VOLATILITY_BREAKOUT.value},
    )
    oms.create_order_intent.assert_awaited_once()
    intent = oms.create_order_intent.await_args.args[0]
    assert intent.trading_mode == TradingMode.LIVE
    assert intent.side == OrderSide.BUY
    assert result["trade"]["entry_order_id"] == "OMS-B-LIVE-ENTRY"
    service._record_execution.assert_not_awaited()


def test_strategy_b_global_paper_remains_paper():
    service, _, _ = _service()
    service.config.mode = AutoTradingMode.PAPER
    assert service._execution_mode_for_signal(_signal()) == AutoTradingMode.PAPER


@pytest.mark.asyncio
async def test_strategy_b_signal_path_captures_evidence_and_dispatches_live_after_promotion():
    service, repo, oms = _service()
    signal = _signal()
    contract = _selected_contract()
    chain = {
        "source": "BREEZE", "captured_at": "2026-09-20T10:00:00+00:00",
        "spot_price": 25001, "expiry": "2026-09-24",
        "strikes": [{"strike": 25000, "call": {
            "instrument_id": "OPT-B-1", "instrument_token": "TOKEN-B-1",
            "expiry": "2026-09-24", "bid": 51, "ask": 52, "ltp": 51.5,
            "open_interest": 50000, "volume": 1200, "lot_size": 50,
        }}],
    }
    repo.get_active_trades = AsyncMock(return_value=[])
    repo.list_trades = AsyncMock(return_value=[])
    repo.save_strategy_signal = AsyncMock()
    repo.save_trade = AsyncMock()
    service.config.mode = AutoTradingMode.LIVE
    service.config.system_armed = True
    service._live_orders_enabled = Mock(return_value=True)
    oms.create_order_intent = AsyncMock(
        return_value=SimpleNamespace(order_id="OMS-B-LIVE-ENTRY")
    )
    service._gather_features = AsyncMock(return_value=MarketFeatures(spot_price=25001, data_ready=True))
    service._save_runtime = AsyncMock()
    service._log_decision = AsyncMock()
    service._record_execution = AsyncMock()
    service._get_option_chain = AsyncMock(return_value=chain)
    service.position_manager.is_within_entry_window = Mock(return_value=True)
    service.position_manager.calculate_position_size = Mock(return_value=(1, 50))
    service.strategy_a.evaluate = Mock(return_value=None)
    service.strategy_b.evaluate = Mock(return_value=signal)
    service.contract_selector.select_contract = Mock(return_value=(contract, [{
        "strike": 25000, "option_type": "CALL", "ask": 52, "bid": 51,
        "oi": 50000, "volume": 1200, "spread_pct": 1.94,
        "instrument_id": "OPT-B-1", "expiry": "2026-09-24", "lot_size": 50,
        "ltp": 51.5, "instrument_token": "TOKEN-B-1", "status": "ELIGIBLE",
    }], None))

    result = await service._evaluate_cycle()

    assert result["status"] == "TRADE_OPENED"
    assert result["trade"]["mode"] == AutoTradingMode.LIVE.value
    snapshot = repo.save_option_chain_snapshot.await_args.args[0]
    assert snapshot["strategy"] == StrategyName.VOLATILITY_BREAKOUT.value
    assert snapshot["execution_mode"] == AutoTradingMode.LIVE.value
    assert snapshot["selected_contract"]["instrument_id"] == "OPT-B-1"
    oms.create_order_intent.assert_not_awaited()


@pytest.mark.asyncio
async def test_strategy_b_snapshot_persists_selector_and_quote_provenance():
    service, repo, _ = _service()
    contract = SelectedContract(
        instrument_id="OPT-B-1", symbol="NIFTY 25000 CALL", expiry="2026-09-24",
        strike=25000, option_type=OptionType.CALL, ask_price=52, bid_price=51,
        open_interest=50000, volume=1200, spread_pct=1.94, lot_size=50,
        ltp=51.5, instrument_token="TOKEN-B-1", premium=52,
    )
    chain = {
        "source": "BREEZE", "captured_at": "2026-09-20T10:00:00+00:00",
        "spot_price": 25001, "expiry": "2026-09-24",
        "strikes": [{"strike": 25000, "call": {
            "instrument_id": "OPT-B-1", "instrument_token": "TOKEN-B-1",
            "expiry": "2026-09-24", "bid": 51, "ask": 52, "ltp": 51.5,
            "open_interest": 50000, "volume": 1200, "lot_size": 50,
        }}],
    }
    await service._capture_option_chain_snapshot(
        signal=_signal(), spot_price=25001, chain=chain,
        selector_candidates=[{
            "strike": 25000, "option_type": "CALL", "ask": 52,
            "bid": 51, "oi": 50000, "volume": 1200, "spread_pct": 1.94,
            "instrument_id": "OPT-B-1", "expiry": "2026-09-24", "lot_size": 50,
            "ltp": 51.5, "instrument_token": "TOKEN-B-1", "status": "ELIGIBLE",
        }], selected_contract=contract, rejection_reason=None,
        execution_mode=AutoTradingMode.SHADOW_ONLY,
    )
    snapshot = repo.save_option_chain_snapshot.await_args.args[0]
    assert snapshot["strategy"] == StrategyName.VOLATILITY_BREAKOUT.value
    assert snapshot["execution_mode"] == AutoTradingMode.SHADOW_ONLY.value
    assert snapshot["strategy_signal_id"] == "SIG-B-FORWARD-1"
    assert snapshot["chain_snapshot_timestamp"] == chain["captured_at"]
    assert snapshot["selected_contract"]["instrument_id"] == "OPT-B-1"
    assert snapshot["selected_contract"]["instrument_token"] == "TOKEN-B-1"
    assert snapshot["chain_candidates"][0]["bid"] == 51
    assert snapshot["chain_candidates"][0]["ask"] == 52
    assert snapshot["chain_candidates"][0]["ltp"] == 51.5
    assert snapshot["chain_candidates"][0]["open_interest"] == 50000


@pytest.mark.asyncio
async def test_strategy_b_snapshot_fields_survive_repository_round_trip(tmp_path):
    repo = StrategyRepository(db_path=tmp_path / "strategy.db")
    await repo.initialize()
    await repo.save_option_chain_snapshot({
        "snapshot_id": "OPTCHAIN-ROUNDTRIP",
        "strategy_signal_id": "SIG-B-FORWARD-1",
        "captured_at": "2026-09-20T10:00:01+00:00",
        "chain_snapshot_timestamp": "2026-09-20T10:00:00+00:00",
        "selector_timestamp": "2026-09-20T10:00:01+00:00",
        "signal_timestamp": "2026-09-20T10:00:00+00:00",
        "strategy": StrategyName.VOLATILITY_BREAKOUT.value,
        "execution_mode": AutoTradingMode.SHADOW_ONLY.value,
        "direction": TradeDirection.BULLISH.value,
        "spot_price": 25001,
        "source": "BREEZE",
        "selector_candidates": [],
        "chain_candidates": [],
        "selected_contract": {"instrument_id": "OPT-B-1", "bid": 51, "ask": 52, "ltp": 51.5, "open_interest": 50000},
        "selector_result": "SELECTED",
    })
    saved = (await repo.list_option_chain_snapshots())[0]
    assert saved["strategy"] == StrategyName.VOLATILITY_BREAKOUT.value
    assert saved["execution_mode"] == AutoTradingMode.SHADOW_ONLY.value
    assert saved["chain_snapshot_timestamp"] == "2026-09-20T10:00:00+00:00"
    assert saved["selected_contract"]["instrument_id"] == "OPT-B-1"

@pytest.mark.asyncio
async def test_strategy_b_live_entry_records_confirmed_broker_fill_only():
    service, repo, oms = _service()
    now = datetime(2026, 9, 20, 10, 5, tzinfo=timezone.utc)
    trade = service.position_manager.create_test_trade if False else None
    from services.strategy.models import ActiveTrade, TradeLifecycleState

    trade = ActiveTrade(
        trade_id="TRD-B-LIVE-FILL",
        mode=AutoTradingMode.LIVE,
        strategy=StrategyName.VOLATILITY_BREAKOUT,
        direction=TradeDirection.BULLISH,
        option_type=OptionType.CALL,
        contract_symbol="NIFTY26SEP25000CE",
        contract_instrument_id="OPT-B-1",
        expiry="2026-09-24",
        strike=25000,
        quantity=50,
        lot_size=50,
        lots=1,
        entry_time=now,
        entry_option_price=52.0,
        entry_spot_price=25000.0,
        initial_structural_stop=24900.0,
        initial_r_points=100.0,
        current_option_price=52.0,
        current_spot_price=25000.0,
        current_trailing_stop=24900.0,
        option_hard_stop_price=39.0,
        state=TradeLifecycleState.ENTRY_PENDING,
        entry_order_id="OMS-B-LIVE-ENTRY",
        entry_bid=51.0,
        entry_ask=52.0,
        entry_ltp=51.5,
    )
    order = SimpleNamespace(
        order_id="OMS-B-LIVE-ENTRY",
        broker_order_id="BRK-B-ENTRY",
        status=SimpleNamespace(value="FILLED"),
        quantity=50,
        filled_quantity=50,
        average_price=52.25,
        updated_at=now,
        trading_mode=TradingMode.LIVE,
    )
    oms.get_order = AsyncMock(return_value=order)
    repo.save_trade = AsyncMock()
    service._record_execution = AsyncMock()
    service._sync_live_protective_stop = AsyncMock(return_value=False)

    await service._evaluate_active_trade(
        trade,
        MarketFeatures(
            timestamp=now,
            spot_price=25000.0,
            data_ready=True,
        ),
    )

    assert trade.state == TradeLifecycleState.OPEN_INITIAL_RISK
    assert trade.filled_quantity == 50
    assert trade.entry_option_price == 52.25
    service._record_execution.assert_awaited_once()
    ledger = service._record_execution.await_args.args[0]
    assert ledger["ledger_id"] == "LIVE-ENTRY-OMS-B-LIVE-ENTRY-50"
    assert ledger["quantity"] == 50
    assert ledger["executable_price"] == 52.25
    assert ledger["source"] == "BROKER_FILL"


@pytest.mark.asyncio
async def test_strategy_b_live_t1_routes_reduce_only_order():
    service, repo, oms = _service()
    now = datetime(2026, 9, 20, 10, 10, tzinfo=timezone.utc)
    from services.strategy.models import ActiveTrade, TradeLifecycleState

    trade = ActiveTrade(
        trade_id="TRD-B-LIVE-T1",
        mode=AutoTradingMode.LIVE,
        strategy=StrategyName.VOLATILITY_BREAKOUT,
        direction=TradeDirection.BULLISH,
        option_type=OptionType.CALL,
        contract_symbol="NIFTY26SEP25000CE",
        contract_instrument_id="OPT-B-1",
        expiry="2026-09-24",
        strike=25000,
        quantity=100,
        lot_size=50,
        lots=2,
        entry_time=now,
        entry_option_price=50.0,
        entry_spot_price=25000.0,
        initial_structural_stop=24900.0,
        initial_r_points=100.0,
        current_option_price=70.0,
        current_spot_price=25150.0,
        current_trailing_stop=25000.0,
        option_hard_stop_price=37.5,
        state=TradeLifecycleState.OPEN_INITIAL_RISK,
        filled_quantity=100,
        initial_quantity=100,
        remaining_quantity=100,
        t1_exit_quantity=50,
    )
    service._resolve_option_quote = AsyncMock(
        return_value={
            "status": "VALID",
            "bid": 70.0,
            "ask": 70.5,
            "ltp": 70.25,
            "source": "BREEZE",
        }
    )
    service._sync_live_protective_stop = AsyncMock(return_value=True)
    service._cancel_live_protective_stop_for_exit = AsyncMock(return_value=True)
    service.position_manager.update_position = Mock(
        return_value=(trade, "T1_PARTIAL_EXIT")
    )
    oms.create_order_intent = AsyncMock(
        return_value=SimpleNamespace(order_id="OMS-B-T1")
    )
    repo.save_trade = AsyncMock()

    await service._evaluate_active_trade(
        trade,
        MarketFeatures(
            timestamp=now,
            spot_price=25150.0,
            data_ready=True,
        ),
    )

    oms.create_order_intent.assert_awaited_once()
    intent = oms.create_order_intent.await_args.args[0]
    assert intent.side == OrderSide.SELL
    assert intent.trading_mode == TradingMode.LIVE
    assert intent.reduce_only is True
    assert intent.quantity == 50
    assert trade.partial_exit_order_id == "OMS-B-T1"

