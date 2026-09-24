from datetime import datetime, timezone
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


def test_strategy_b_global_live_resolves_shadow_only():
    service, _, _ = _service()
    service.config.mode = AutoTradingMode.LIVE
    assert service._execution_mode_for_signal(_signal()) == AutoTradingMode.SHADOW_ONLY


@pytest.mark.asyncio
async def test_strategy_b_live_compat_signal_never_dispatches_to_oms():
    service, _, oms = _service()
    await service.emit_signal(
        "INST", "NIFTY-25000-CE", "OPT-B-1", side=OrderSide.BUY,
        suggested_price=50, quantity=50, trading_mode=TradingMode.LIVE,
        metadata={"strategy": StrategyName.VOLATILITY_BREAKOUT.value},
    )
    oms.create_order_intent.assert_not_awaited()


def test_strategy_b_global_paper_remains_paper():
    service, _, _ = _service()
    service.config.mode = AutoTradingMode.PAPER
    assert service._execution_mode_for_signal(_signal()) == AutoTradingMode.PAPER


@pytest.mark.asyncio
async def test_strategy_b_signal_path_captures_evidence_and_stays_shadow_in_global_live():
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
    assert result["trade"]["mode"] == AutoTradingMode.SHADOW_ONLY.value
    snapshot = repo.save_option_chain_snapshot.await_args.args[0]
    assert snapshot["strategy"] == StrategyName.VOLATILITY_BREAKOUT.value
    assert snapshot["execution_mode"] == AutoTradingMode.SHADOW_ONLY.value
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
