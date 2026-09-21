from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call

import pytest

from libs.contracts.models import Candle
from services.strategy.futures_signal import (
    FuturesContractResolver, FuturesFeatureEngine, FuturesFeatureSnapshot,
    canonical_active_futures_stream, canonical_active_futures_stream_with_diagnostics,
    resolve_active_futures_instrument,
)
from services.strategy.reason_codes import (
    OPTION_EMERGENCY_STOP, OPTION_EMERGENCY_STOP_OUTCOME_STATUS,
    OPTION_EMERGENCY_STOP_UNDERLYING_REASON,
)
from services.strategy.models import (
    ActiveTrade, AutoTradingMode, MarketFeatures, OptionType, StrategyDirection,
    SelectedContract, StrategyName, StrategyState, StrategyStateSnapshot, StrategySetup, StrategyTunablesConfig,
    ThresholdOverrides, TradeDirection, TradeLifecycleState,
)
from services.strategy.position_manager import PositionManager, calculate_realized_trade_r
from services.strategy.replay_strategy_a import StrategyAReplayEngine
from services.strategy.service import StrategyService
from services.strategy.strategies.trend_pullback import TrendPullbackStrategy
from services.strategy.telemetry import StrategyAEvaluationRecord, StrategyATelemetryStore

UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))


def _trade(*, lots=2, direction=TradeDirection.BULLISH):
    entry, risk = 100.0, 10.0
    stop = entry - risk if direction is TradeDirection.BULLISH else entry + risk
    return ActiveTrade(
        trade_id="HARDEN-1", mode=AutoTradingMode.PAPER,
        strategy=StrategyName.TREND_PULLBACK, direction=direction,
        option_type=OptionType.CALL if direction is TradeDirection.BULLISH else OptionType.PUT,
        contract_symbol="NIFTY-HARDEN", contract_instrument_id="OPT-HARDEN", expiry="2026-09-24",
        strike=24000, quantity=lots * 75, lot_size=75, lots=lots,
        entry_time=datetime(2026, 9, 21, 9, 45, tzinfo=IST), entry_option_price=100,
        entry_spot_price=entry, initial_structural_stop=stop, initial_r_points=risk,
        current_option_price=100, current_spot_price=entry, current_trailing_stop=stop,
        option_hard_stop_price=0, state=TradeLifecycleState.OPEN_INITIAL_RISK,
        selected_contract_snapshot={"instrument_id": "OPT-HARDEN", "symbol": "NIFTY-HARDEN"},
        entry_raw_ask=100, entry_executable_price=101, entry_slippage_points=1,
        futures_contract_id="FUT-NEAR", underlying_entry_price=entry,
        underlying_current_price=entry, underlying_structural_stop=stop,
        underlying_r=risk, initial_quantity=lots * 75, remaining_quantity=lots * 75,
    )


def _features(price, timestamp):
    return MarketFeatures(timestamp=timestamp, spot_price=price, futures_price=price)


def _service(quote=None, slippage=2.0):
    repo = Mock(
        save_trade=AsyncMock(), save_runtime=AsyncMock(), save_decision_log=AsyncMock(),
        save_execution_ledger=AsyncMock(), save_signal=AsyncMock(), save_option_quote=AsyncMock(),
    )
    market = Mock(get_latest_quote=Mock(return_value=quote))
    service = StrategyService(SimpleNamespace(create_order_intent=AsyncMock()), repository=repo,
                              market_data_service=market, event_bus=Mock(publish=AsyncMock()))
    service.config.risk.paper_slippage_points = slippage
    return service, repo


def _quote(timestamp, bid=100.0):
    return SimpleNamespace(
        source="BREEZE", instrument_id="OPT-HARDEN", symbol="NIFTY-HARDEN",
        last_price=bid, best_bid=bid, best_ask=bid + 1, volume=1000,
        open_interest=50000, timestamp=timestamp,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", [TradeDirection.BULLISH, TradeDirection.BEARISH])
async def test_real_position_manager_option_emergency_stop_closes_without_fake_underlying_exit(direction):
    at = datetime(2026, 9, 21, 10, 0, tzinfo=IST)
    service, _ = _service(_quote(datetime.now(UTC), 89), slippage=2.0)
    trade = _trade(lots=1, direction=direction).model_copy(update={"option_hard_stop_price": 95})
    await service._evaluate_active_trade(trade, _features(100, at))
    assert trade.state is TradeLifecycleState.CLOSED
    assert trade.exit_reason == OPTION_EMERGENCY_STOP
    assert trade.option_exit_reason == OPTION_EMERGENCY_STOP
    assert trade.underlying_exit_reason == OPTION_EMERGENCY_STOP_UNDERLYING_REASON
    assert trade.underlying_exit_time is None
    assert trade.underlying_exit_price is None
    assert trade.underlying_outcome_status == OPTION_EMERGENCY_STOP_OUTCOME_STATUS
    assert trade.realized_r is None
    assert trade.exit_option_price == 87
    assert service.oms.create_order_intent.await_count == 0
    closed_records = [record for record in service.strategy_a_telemetry.records if record.management_event == "CLOSED"]
    assert closed_records[-1].exit_reason == OPTION_EMERGENCY_STOP
    assert closed_records[-1].underlying_outcome_status == OPTION_EMERGENCY_STOP_OUTCOME_STATUS
    assert closed_records[-1].realized_r is None


@pytest.mark.asyncio
@pytest.mark.parametrize("direction", [TradeDirection.BULLISH, TradeDirection.BEARISH])
async def test_option_emergency_stop_after_t1_preserves_t1_r_but_runner_r_is_unresolved(direction):
    first = datetime(2026, 9, 21, 10, 0, tzinfo=IST)
    service, _ = _service(_quote(datetime.now(UTC), 100), slippage=2.0)
    trade = _trade(lots=2, direction=direction).model_copy(update={"option_hard_stop_price": 95})
    await service._evaluate_active_trade(trade, _features(115 if direction is TradeDirection.BULLISH else 85, first))
    assert trade.partial_exit_filled_quantity == 75
    service.mkt_svc.get_latest_quote.return_value = _quote(datetime.now(UTC), 89)
    await service._evaluate_active_trade(trade, _features(115 if direction is TradeDirection.BULLISH else 85, first + timedelta(minutes=5)))
    assert trade.exit_reason == OPTION_EMERGENCY_STOP
    assert trade.t1_realized_r == 1.5
    assert trade.runner_realized_r is None
    assert trade.realized_r is None
    assert trade.underlying_outcome_status == OPTION_EMERGENCY_STOP_OUTCOME_STATUS


@pytest.mark.asyncio
async def test_option_emergency_stop_with_missing_bid_is_pending_without_synthetic_fill():
    at = datetime(2026, 9, 21, 10, 0, tzinfo=IST)
    missing_bid = SimpleNamespace(
        source="BREEZE", instrument_id="OPT-HARDEN", symbol="NIFTY-HARDEN",
        last_price=89, best_bid=0, best_ask=90, volume=1000, open_interest=50000, timestamp=datetime.now(UTC),
    )
    service, repo = _service(missing_bid, slippage=2.0)
    trade = _trade(lots=1).model_copy(update={"option_hard_stop_price": 95})
    await service._evaluate_active_trade(trade, _features(100, at))
    assert trade.state is not TradeLifecycleState.CLOSED
    assert trade.pending_exit_reason == OPTION_EMERGENCY_STOP
    assert trade.underlying_exit_reason == OPTION_EMERGENCY_STOP_UNDERLYING_REASON
    assert not repo.save_execution_ledger.await_args_list


@pytest.mark.asyncio
async def test_exit_precedence_pending_then_forced_then_option_stop_then_underlying_stop_then_t1():
    at = datetime(2026, 9, 21, 15, 15, tzinfo=IST)
    service, _ = _service(_quote(datetime.now(UTC), 89), slippage=2.0)
    trade = _trade(lots=1).model_copy(update={"option_hard_stop_price": 95})
    await service._evaluate_active_trade(trade, _features(100, at))
    assert trade.exit_reason == "SESSION_FORCE_SQUARE_OFF_1515"


@pytest.mark.asyncio
async def test_strategy_a_structural_stop_is_decided_without_option_quote_and_later_bid_closes_original_reason():
    service, repo = _service(None)
    trade = _trade(lots=1)
    decision_time = datetime(2026, 9, 21, 10, 0, tzinfo=IST)
    await service._evaluate_active_trade(trade, _features(89, decision_time))
    assert trade.pending_exit_reason == "UNDERLYING_STRUCTURAL_STOP"
    assert trade.pending_underlying_exit_time == decision_time
    assert trade.state is not TradeLifecycleState.CLOSED
    assert not repo.save_execution_ledger.await_args_list

    service.mkt_svc.get_latest_quote.return_value = _quote(datetime.now(UTC), 100)
    await service._evaluate_active_trade(trade, _features(120, decision_time + timedelta(minutes=5)))
    assert trade.state is TradeLifecycleState.CLOSED
    assert trade.exit_reason == "UNDERLYING_STRUCTURAL_STOP"
    assert trade.underlying_exit_price == 89
    assert trade.exit_option_price == 98


@pytest.mark.asyncio
async def test_strategy_a_trailing_stop_and_force_exit_are_pending_without_quote():
    service, _ = _service(None)
    trade = _trade(lots=1)
    first = datetime(2026, 9, 21, 10, 0, tzinfo=IST)
    await service._evaluate_active_trade(trade, _features(115, first))
    assert trade.pending_exit_reason is None
    await service._evaluate_active_trade(trade, _features(104, first + timedelta(minutes=5)))
    assert trade.pending_exit_reason == "UNDERLYING_TRAILING_STOP"

    force_trade = _trade(lots=1)
    force_time = datetime(2026, 9, 21, 9, 45, tzinfo=IST).replace(hour=15, minute=15).astimezone(UTC)
    await service._evaluate_active_trade(force_trade, _features(100, force_time))
    assert force_trade.pending_exit_reason == "SESSION_FORCE_SQUARE_OFF_1515"


@pytest.mark.asyncio
async def test_strategy_a_t1_decision_without_quote_preserves_quantity_then_fills_once_with_slippage():
    service, _ = _service(None, slippage=2.0)
    trade = _trade(lots=2)
    at = datetime(2026, 9, 21, 10, 0, tzinfo=IST)
    await service._evaluate_active_trade(trade, _features(115, at))
    assert trade.t1_exit_pending is True
    assert trade.t1_exit_quantity == 75
    assert trade.quantity == 150
    assert trade.partial_exit_filled_quantity == 0

    service.mkt_svc.get_latest_quote.return_value = _quote(datetime.now(UTC), 100)
    await service._evaluate_active_trade(trade, _features(115, at + timedelta(minutes=5)))
    assert trade.partial_exit_filled_quantity == 75
    assert trade.quantity == 75
    assert trade.partial_exit_raw_bid == 100
    assert trade.partial_exit_price == 98
    assert trade.partial_exit_slippage_points == 2
    await service._evaluate_active_trade(trade, _features(115, at + timedelta(minutes=10)))
    assert trade.partial_exit_filled_quantity == 75
    assert len([x for x in service.repo.save_execution_ledger.await_args_list if x.args[0]["reason"] == "T1_REACHED_PARTIAL_EXIT"]) == 1


def test_weighted_realized_r_is_canonical_and_call_put_symmetric():
    for direction in (TradeDirection.BULLISH, TradeDirection.BEARISH):
        entry = 100.0
        sign = 1 if direction is TradeDirection.BULLISH else -1
        assert calculate_realized_trade_r(direction, entry, 10, 2, [(1, entry + sign * 15), (1, entry)]) == 0.75
        assert calculate_realized_trade_r(direction, entry, 10, 4, [(2, entry + sign * 15), (2, entry + sign * 25)]) == 2.0
        assert calculate_realized_trade_r(direction, entry, 10, 3, [(1, entry + sign * 15), (2, entry - sign * 5)]) == 0.1667


def test_replay_uses_the_same_weighted_r_helper_for_partial_outcomes():
    signal = SimpleNamespace(underlying_entry_price=100, spot_reference_price=100)
    # The production helper is exercised through the replay engine's finalizer.
    trade = _trade(lots=2)
    trade.t1_decision_underlying_price = 115
    trade.partial_exit_filled_quantity = 75
    trade.quantity = 75
    assert StrategyAReplayEngine._realized_r(trade, 100) == 0.75


def test_partial_transaction_costs_use_three_execution_legs_and_actual_quantities():
    service, _ = _service(None, slippage=2.0)
    trade = _trade(lots=2)
    pm = PositionManager()
    pm.update_position(trade, 100, _features(115, datetime(2026, 9, 21, 10, 0, tzinfo=UTC)))
    pm.apply_t1_partial_fill(trade, raw_bid=110, executable_price=108, slippage_points=2, filled_at=datetime.now(UTC))
    import asyncio
    asyncio.run(service._close_trade(trade, _features(100, datetime(2026, 9, 21, 10, 5, tzinfo=UTC)), 93, "UNDERLYING_TRAILING_STOP", quote={"bid": 95, "source": "BREEZE"}))
    assert trade.execution_order_count == 3
    assert trade.final_exit_quantity == 75
    assert trade.transaction_costs > 0
    assert trade.raw_gross_option_pnl == round((95 - 100) * 75 + (110 - 100) * 75, 2)


def test_one_lot_has_no_partial_order_and_realized_r_is_final_exit_r():
    service, _ = _service(None)
    trade = _trade(lots=1)
    import asyncio
    asyncio.run(service._close_trade(trade, _features(110, datetime(2026, 9, 21, 10, 5, tzinfo=UTC)), 98, "UNDERLYING_STRUCTURAL_STOP", quote={"bid": 100, "source": "BREEZE"}))
    assert trade.execution_order_count == 2
    assert trade.partial_exit_filled_quantity == 0
    assert trade.realized_r == 1.0


def _bar(instrument, timestamp):
    return Candle(instrument_id=instrument, interval="15m", start_time=timestamp - timedelta(minutes=15),
                  end_time=timestamp, open=100, high=110, low=90, close=105, volume=100, source="BREEZE")


def test_canonical_replay_stream_selects_one_contract_per_timestamp_and_one_real_rollover():
    sep, octo = "NIFTY-FUT-2026-09-24", "NIFTY-FUT-2026-10-01"
    t1 = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)
    stream = canonical_active_futures_stream([_bar(sep, t1), _bar(octo, t1), _bar(sep, t2), _bar(octo, t2)])
    assert [x.instrument_id for x in stream] == [sep, octo]
    report = StrategyAReplayEngine().replay([_bar(sep, t1), _bar(octo, t1), _bar(sep, t2), _bar(octo, t2)])
    assert report.rejection_reasons.get("FUTURES_ROLLOVER_RESET", 0) == 1


def test_missing_active_near_contract_is_data_gap_not_false_rollover_or_next_contract_substitution():
    sep, octo = "NIFTY-FUT-2026-09-24", "NIFTY-FUT-2026-10-01"
    t1 = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 9, 21, 10, 15, tzinfo=UTC)
    t3 = datetime(2026, 9, 21, 10, 30, tzinfo=UTC)
    bars, gaps = canonical_active_futures_stream_with_diagnostics(
        [_bar(sep, t1), _bar(octo, t1), _bar(octo, t2), _bar(sep, t3), _bar(octo, t3)]
    )
    assert [bar.instrument_id for bar in bars] == [sep, sep]
    assert [gap.timestamp for gap in gaps] == [t2]
    report = StrategyAReplayEngine().replay(
        [_bar(sep, t1), _bar(octo, t1), _bar(octo, t2), _bar(sep, t3), _bar(octo, t3)]
    )
    assert report.data_quality_counts == {"ACTIVE_FUTURES_CANDLE_MISSING": 1}
    assert report.rejection_reasons.get("FUTURES_ROLLOVER_RESET", 0) == 0


def test_expired_near_contract_is_not_fallback_and_multiple_later_contracts_use_nearest_valid():
    sep, octo, nov = "NIFTY-FUT-2026-09-24", "NIFTY-FUT-2026-10-01", "NIFTY-FUT-2026-10-29"
    before = datetime(2026, 9, 23, 10, 0, tzinfo=UTC)
    after = datetime(2026, 9, 25, 10, 0, tzinfo=UTC)
    bars, gaps = canonical_active_futures_stream_with_diagnostics(
        [_bar(sep, before), _bar(octo, before), _bar(nov, before), _bar(sep, after), _bar(nov, after)]
    )
    assert [bar.instrument_id for bar in bars] == [sep]
    assert [gap.expected_contract for gap in gaps] == [octo]
    bars, gaps = canonical_active_futures_stream_with_diagnostics(
        [_bar(sep, before), _bar(octo, before), _bar(nov, before), _bar(octo, after), _bar(nov, after)]
    )
    assert [bar.instrument_id for bar in bars] == [sep, octo]
    assert not gaps


def test_runtime_metadata_and_replay_contract_resolution_are_identical():
    sep, octo = "NIFTY-FUT-2026-09-24", "NIFTY-FUT-2026-10-01"
    at = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    instruments = [
        SimpleNamespace(segment="FUTURES", tradable=True, expiry="2026-09-24", instrument_id=sep),
        SimpleNamespace(segment="FUTURES", tradable=True, expiry="2026-10-01", instrument_id=octo),
    ]
    candles = [_bar(sep, at), _bar(octo, at)]
    runtime_id = resolve_active_futures_instrument(instruments, as_of=at)
    replay_id = FuturesContractResolver().resolve(candles, as_of=at)
    stream, _ = canonical_active_futures_stream_with_diagnostics(candles, as_of=at)
    assert runtime_id == replay_id == stream[0].instrument_id == sep


def test_pivot_is_absent_until_two_right_confirmation_bars_and_confirmed_at_second_right_bar():
    start = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
    def make(count):
        return [Candle(instrument_id="NIFTY-FUT-2026-09-24", interval="15m", start_time=start + timedelta(minutes=15 * i), end_time=start + timedelta(minutes=15 * (i + 1)), open=100, high=110 if i == 2 else 105, low=90 if i == 2 else 95, close=100, volume=100, source="BREEZE") for i in range(count)]
    assert FuturesFeatureEngine.confirmed_pivots(make(3)) == []
    assert FuturesFeatureEngine.confirmed_pivots(make(4)) == []
    pivots = FuturesFeatureEngine.confirmed_pivots(make(5))
    assert pivots and pivots[0].confirmed_at == make(5)[4].end_time


def test_phase8_trend_di_adx_and_ema_separation_exact_boundaries():
    strategy = TrendPullbackStrategy()
    base = dict(contract_id="FUT", candle_timestamp=datetime(2026, 9, 21, 10, 0, tzinfo=UTC), candle_start=datetime(2026, 9, 21, 9, 45, tzinfo=UTC), open=100, high=110, low=90, close=108, ema20=101, ema50=100, adx14=22, plus_di14=30, minus_di14=10, atr14=10, session_vwap=100, bar_index=10)
    assert strategy._trend_ok(FuturesFeatureSnapshot(**base), StrategyDirection.CALL)[0]
    assert not strategy._trend_ok(FuturesFeatureSnapshot(**{**base, "plus_di14": 10, "minus_di14": 30}), StrategyDirection.CALL)[0]
    assert not strategy._trend_ok(FuturesFeatureSnapshot(**{**base, "adx14": 21.999}), StrategyDirection.CALL)[0]
    assert not strategy._trend_ok(FuturesFeatureSnapshot(**{**base, "ema20": 100.999}), StrategyDirection.CALL)[0]


def test_phase8_confirmation_exact_body_close_location_zero_range_and_range_boundaries():
    strategy = TrendPullbackStrategy()
    base = dict(contract_id="FUT", candle_timestamp=datetime(2026, 9, 21, 10, 0, tzinfo=UTC), candle_start=datetime(2026, 9, 21, 9, 45, tzinfo=UTC), open=100, high=104, low=94, close=104, ema20=100, ema50=90, adx14=30, plus_di14=30, minus_di14=10, atr14=10, session_vwap=100, bar_index=10)
    assert strategy._confirmation_ok(FuturesFeatureSnapshot(**base), StrategyDirection.CALL)[0]
    assert not strategy._confirmation_ok(FuturesFeatureSnapshot(**{**base, "close": 103.99}), StrategyDirection.CALL)[0]
    assert not strategy._confirmation_ok(FuturesFeatureSnapshot(**{**base, "high": 115, "low": 100, "open": 104, "close": 110}), StrategyDirection.CALL)[0]
    assert strategy._confirmation_ok(FuturesFeatureSnapshot(**{**base, "high": 115, "low": 100, "open": 104.5, "close": 110.5}), StrategyDirection.CALL)[0]
    assert not strategy._confirmation_ok(FuturesFeatureSnapshot(**{**base, "high": 100, "low": 100, "close": 100}), StrategyDirection.CALL)[0]
    assert strategy._confirmation_ok(FuturesFeatureSnapshot(**{**base, "high": 115, "low": 100, "close": 115}), StrategyDirection.CALL)[0]
    assert not strategy._confirmation_ok(FuturesFeatureSnapshot(**{**base, "high": 115.01, "low": 100, "close": 115.01}), StrategyDirection.CALL)[0]


def test_phase8_confluence_explicit_ema_vwap_sr_and_pivot_confirmation_cases():
    strategy = TrendPullbackStrategy()
    base = dict(contract_id="FUT", candle_timestamp=datetime(2026, 9, 21, 10, 0, tzinfo=UTC), candle_start=datetime(2026, 9, 21, 9, 45, tzinfo=UTC), open=100, high=110, low=100, close=100, ema20=100, ema50=90, adx14=30, plus_di14=30, minus_di14=10, atr14=10, session_vwap=100, support=100, bar_index=10)
    assert strategy._confluence(FuturesFeatureSnapshot(**base), StrategyDirection.CALL)[0]
    assert strategy._confluence(FuturesFeatureSnapshot(**{**base, "session_vwap": 120}), StrategyDirection.CALL)[0]  # EMA20 only
    assert strategy._confluence(FuturesFeatureSnapshot(**{**base, "ema20": 120}), StrategyDirection.CALL)[0]
    assert strategy._confluence(FuturesFeatureSnapshot(**{**base, "ema20": 120, "session_vwap": 120}), StrategyDirection.CALL)[0] is False
    assert strategy._confluence(FuturesFeatureSnapshot(**{**base, "support": None}), StrategyDirection.CALL)[3] == "NO_CONFIRMED_SR"
    assert strategy._confluence(FuturesFeatureSnapshot(**{**base, "support": 90}), StrategyDirection.CALL)[3] == "PRICE_NOT_IN_SR_ZONE"


def test_phase8_structural_r_and_opposing_room_exact_boundaries():
    strategy = TrendPullbackStrategy()
    base = dict(contract_id="FUT", candle_timestamp=datetime(2026, 9, 21, 10, 0, tzinfo=UTC), candle_start=datetime(2026, 9, 21, 9, 45, tzinfo=UTC), open=100, high=100, low=93.5, close=99, ema20=100, ema50=90, adx14=30, plus_di14=30, minus_di14=10, atr14=10, session_vwap=99, support=93.5, resistance=None, bar_index=10)
    setup, reason = strategy._build_setup(FuturesFeatureSnapshot(**base), StrategyDirection.CALL, ["CONFIRMED_SR"], 93.5)
    assert reason is None and setup is not None and setup.initial_underlying_r == 8.0
    assert strategy._build_setup(FuturesFeatureSnapshot(**{**base, "low": 93.51, "support": 93.51}), StrategyDirection.CALL, ["CONFIRMED_SR"], 93.51)[1] == "STRUCTURAL_R_BELOW_MINIMUM"
    high_r = {**base, "low": 86.5, "support": 86.5}
    assert strategy._build_setup(FuturesFeatureSnapshot(**high_r), StrategyDirection.CALL, ["CONFIRMED_SR"], 86.5)[1] is None
    too_high = {**high_r, "low": 86.49, "support": 86.49}
    assert strategy._build_setup(FuturesFeatureSnapshot(**too_high), StrategyDirection.CALL, ["CONFIRMED_SR"], 86.49)[1] == "STRUCTURAL_R_ABOVE_MAXIMUM"
    room = {**base, "resistance": 112.5}
    assert strategy._build_setup(FuturesFeatureSnapshot(**room), StrategyDirection.CALL, ["CONFIRMED_SR"], 93.5)[1] is None
    below_room = {**room, "resistance": 112.49}
    assert strategy._build_setup(FuturesFeatureSnapshot(**below_room), StrategyDirection.CALL, ["CONFIRMED_SR"], 93.5)[1] == "INSUFFICIENT_ROOM_TO_OPPOSING_SR"


def test_phase8_pivot_confirmation_trigger_duplicate_and_session_boundaries_are_explicit():
    start = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
    candles = [Candle(instrument_id="NIFTY-FUT-2026-09-24", interval="15m", start_time=start + timedelta(minutes=15 * i), end_time=start + timedelta(minutes=15 * (i + 1)), open=100, high=(110 if i == 2 else 105), low=(90 if i == 2 else 95), close=100, volume=100, source="BREEZE") for i in range(5)]
    pivots = FuturesFeatureEngine.confirmed_pivots(candles)
    assert pivots and pivots[0].confirmed_at == candles[4].end_time
    strategy = TrendPullbackStrategy()
    clock = SimpleNamespace(timestamp=candles[-1].end_time)
    strategy.evaluate(clock, [], [], futures_candles=candles)
    strategy.evaluate(clock, [], [], futures_candles=candles)
    assert strategy.last_event.event == "DUPLICATE_IGNORED"
    assert strategy._entry_allowed(datetime(2026, 9, 21, 9, 45, tzinfo=IST), None)
    assert not strategy._entry_allowed(datetime(2026, 9, 21, 14, 46, tzinfo=IST), None)
    assert not strategy._forced_exit(datetime(2026, 9, 21, 15, 14, tzinfo=IST))
    assert strategy._forced_exit(datetime(2026, 9, 21, 15, 15, tzinfo=IST))


def test_phase8_trigger_validity_gap_fill_two_bars_expiry_and_chase_boundaries():
    setup_time = datetime(2026, 9, 21, 10, 0, tzinfo=IST)
    setup = StrategySetup(direction=StrategyDirection.CALL, setup_timestamp=setup_time,
                          confirmation_bar_timestamp=setup_time, confirmation_high=100,
                          confirmation_low=95, trigger_price=100, structural_stop=90,
                          initial_underlying_r=10, relevant_support_resistance_level=95,
                          confluence_references=("CONFIRMED_SR",),
                          setup_expiry_timestamp=setup_time + timedelta(minutes=30), setup_expiry_bar_index=2)

    def run(feature):
        strategy = TrendPullbackStrategy()
        strategy.snapshot = StrategyStateSnapshot().transition(StrategyState.SETUP, setup=setup)
        strategy.active_contract_id = feature.contract_id
        strategy._features_for_input = lambda *_args, **_kwargs: ([Candle(instrument_id=feature.contract_id, interval="15m", start_time=feature.candle_start, end_time=feature.candle_timestamp, open=feature.open, high=feature.high, low=feature.low, close=feature.close, volume=100, source="BREEZE")], feature)
        signal = strategy.evaluate(SimpleNamespace(timestamp=feature.candle_timestamp), [], [], futures_candles=[])
        return strategy, signal

    base = dict(contract_id="NIFTY-FUT-2026-09-24", candle_timestamp=setup_time + timedelta(minutes=15), candle_start=setup_time, open=99, high=99, low=95, close=98, ema20=100, ema50=90, adx14=30, plus_di14=30, minus_di14=10, atr14=10, session_vwap=100, bar_index=1)
    strategy, signal = run(FuturesFeatureSnapshot(**{**base, "high": 100}))
    assert signal is not None and signal.underlying_entry_price == 100 and strategy.last_event.reason == "TRIGGER_CROSSED"
    _, gap_signal = run(FuturesFeatureSnapshot(**{**base, "open": 102, "high": 103}))
    assert gap_signal is not None and gap_signal.underlying_entry_price == 102
    first, first_signal = run(FuturesFeatureSnapshot(**{**base, "bar_index": 1}))
    assert first_signal is None
    first.snapshot = first.snapshot.transition(StrategyState.ARMED) if first.snapshot.state is StrategyState.SETUP else first.snapshot
    first._features_for_input = lambda *_args, **_kwargs: ([Candle(instrument_id=base["contract_id"], interval="15m", start_time=setup_time + timedelta(minutes=15), end_time=setup_time + timedelta(minutes=30), open=99, high=100, low=95, close=98, volume=100, source="BREEZE")], FuturesFeatureSnapshot(**{**base, "candle_timestamp": setup_time + timedelta(minutes=30), "candle_start": setup_time + timedelta(minutes=15), "bar_index": 2, "high": 100}))
    second_signal = first.evaluate(SimpleNamespace(timestamp=setup_time + timedelta(minutes=30)), [], [], futures_candles=[])
    assert second_signal is not None
    expired, expired_signal = run(FuturesFeatureSnapshot(**{**base, "bar_index": 3}))
    assert expired_signal is None and expired.last_event.event == "EXPIRED"
    _, exact_chase = run(FuturesFeatureSnapshot(**{**base, "open": 102.5, "high": 103}))
    assert exact_chase is not None
    _, too_much_chase = run(FuturesFeatureSnapshot(**{**base, "open": 102.51, "high": 103}))
    assert too_much_chase is None


def test_strategy_a_telemetry_summary_counts_selection_sizing_execution_and_discrepancies():
    def row(event, **kwargs):
        return StrategyAEvaluationRecord(timestamp="2026-09-21T10:00:00+00:00", futures_contract="FUT", completed_candle_timestamp="2026-09-21T09:45:00+00:00", strategy_state="OPEN", management_event=event, **kwargs)
    store = StrategyATelemetryStore()
    for record in [
        row("EVALUATED"), row("SETUP_CREATED"), row("ARMED"), row("TRIGGERED"),
        row("CONTRACT_SELECTED"), row("CONTRACT_SELECTION_REJECTED", rejection_or_invalidation_reason="STALE_QUOTE"),
        row("SIZING_REJECTED"), row("ENTRY_OPENED", option_pnl=4, slippage_points=2),
        row("PARTIAL_EXIT", slippage_points=2), row("CLOSED", exit_reason="UNDERLYING_STRUCTURAL_STOP", realized_r=0.75, option_pnl=-3),
        row("FORCED_EXIT", exit_reason="SESSION_FORCE_SQUARE_OFF_1515"), row("RUNTIME_REPLAY_DISCREPANCY"),
    ]:
        store.append(record)
    summary = store.summary()
    assert summary["sessions"] == 1
    assert summary["option_selection_attempts"] == 2
    assert summary["option_selection_success_rate"] == 0.5
    assert summary["sizing_rejections"] == 1
    assert summary["partial_exits"] == 1
    assert summary["structural_stop_exits"] == 1
    assert summary["forced_exits"] == 1
    assert summary["option_pnl_total"] == 1
    assert summary["runtime_replay_discrepancy_count"] == 1

def _armed_execution_strategy(at):
    setup_at = at - timedelta(minutes=15)
    setup = StrategySetup(
        direction=StrategyDirection.CALL,
        setup_timestamp=setup_at,
        confirmation_bar_timestamp=setup_at,
        confirmation_high=100,
        confirmation_low=90,
        trigger_price=100,
        structural_stop=90,
        initial_underlying_r=10,
        relevant_support_resistance_level=92,
        confluence_references=("CONFIRMED_SR", "EMA20"),
        setup_expiry_timestamp=at + timedelta(minutes=15),
        setup_expiry_bar_index=3,
    )
    strategy = TrendPullbackStrategy()
    strategy.snapshot = (
        StrategyStateSnapshot()
        .transition(StrategyState.SETUP, setup=setup)
        .transition(StrategyState.ARMED)
    )
    feature = FuturesFeatureSnapshot(
        contract_id="INST-NIFTY-FUT-2026-09-24",
        candle_timestamp=at,
        candle_start=at - timedelta(minutes=15),
        open=100,
        high=101,
        low=95,
        close=100,
        ema20=99,
        ema50=95,
        adx14=30,
        plus_di14=30,
        minus_di14=10,
        atr14=10,
        session_vwap=99,
        support=92,
        resistance=120,
        bar_index=2,
    )
    strategy._features_for_input = lambda *_args, **_kwargs: ([], feature)
    return strategy


def test_strategy_a_trigger_is_not_entered_until_execution_confirmation_and_rejection_recovers():
    at = datetime(2026, 9, 21, 10, 0, tzinfo=IST)
    strategy = _armed_execution_strategy(at)
    signal = strategy.evaluate(SimpleNamespace(timestamp=at), [], [], futures_candles=[])
    assert signal is not None
    assert strategy.snapshot.state is StrategyState.ARMED
    assert strategy.last_event.event == "TRIGGERED"

    strategy.confirm_entry(signal.timestamp)
    assert strategy.snapshot.state is StrategyState.ENTERED
    assert strategy.last_event.reason == "OPTION_EXECUTION_CONFIRMED"

    rejected = _armed_execution_strategy(at)
    rejected_signal = rejected.evaluate(SimpleNamespace(timestamp=at), [], [], futures_candles=[])
    assert rejected_signal is not None
    rejected.on_execution_rejected(rejected_signal.timestamp, "EXECUTION_REJECTED_CONTRACT_SELECTION")
    assert rejected.snapshot.state is StrategyState.COOLDOWN
    assert rejected.last_event.reason == "EXECUTION_REJECTED_CONTRACT_SELECTION"


@pytest.mark.asyncio
async def test_service_execution_rejection_cannot_leave_strategy_a_entered():
    at = datetime(2026, 9, 21, 10, 0, tzinfo=IST)
    service, repo = _service(None)
    strategy = _armed_execution_strategy(at)
    signal = strategy.evaluate(SimpleNamespace(timestamp=at), [], [], futures_candles=[])
    service.strategy_a = strategy

    await service._reject_strategy_a_execution(signal, "EXECUTION_REJECTED_SIZING")

    assert service.strategy_a.snapshot.state is StrategyState.COOLDOWN
    assert service.strategy_a.snapshot.state is not StrategyState.ENTERED
    assert repo.save_runtime.await_count >= 1


@pytest.mark.asyncio
async def test_strategy_a_force_entry_is_disabled_and_cannot_create_validation_trade():
    service, repo = _service(None)
    result = await service.force_entry(
        strategy=StrategyName.TREND_PULLBACK,
        direction=TradeDirection.BULLISH,
        option_type=OptionType.CALL,
    )
    assert result["status"] == "STRATEGY_A_FORCE_ENTRY_DISABLED"
    assert repo.save_trade.await_count == 0


def test_production_strategy_a_cannot_bypass_entry_window_but_simulation_can_opt_in():
    before_open = datetime(2026, 9, 21, 9, 44, tzinfo=IST)
    override = ThresholdOverrides(bypass_entry_window=True)
    production = TrendPullbackStrategy()
    simulation = TrendPullbackStrategy(allow_session_bypass=True)

    assert production._entry_allowed(before_open, override) is False
    assert simulation._entry_allowed(before_open, override) is True


def test_stale_futures_data_is_machine_readable_and_does_not_advance_state():
    end = datetime(2026, 9, 21, 10, 0, tzinfo=IST)
    stale_as_of = datetime(2026, 9, 21, 10, 20, tzinfo=IST)
    candle = Candle(
        instrument_id="INST-NIFTY-FUT-2026-09-24",
        interval="15m",
        start_time=end - timedelta(minutes=15),
        end_time=end,
        open=100,
        high=102,
        low=98,
        close=101,
        volume=100,
        source="BREEZE",
    )
    strategy = TrendPullbackStrategy()
    signal = strategy.evaluate(
        SimpleNamespace(timestamp=stale_as_of),
        [],
        [],
        futures_candles=[candle],
    )
    assert signal is None
    assert strategy.snapshot.state is StrategyState.FLAT
    assert strategy.last_event.reason == "STALE_FUTURES_DATA"


def test_historical_as_of_accepts_the_expected_completed_futures_bar():
    end = datetime(2026, 9, 21, 10, 15, tzinfo=IST)
    candle = Candle(
        instrument_id="INST-NIFTY-FUT-2026-09-24",
        interval="15m",
        start_time=end - timedelta(minutes=15),
        end_time=end,
        open=100,
        high=102,
        low=98,
        close=101,
        volume=100,
        source="BREEZE",
    )
    strategy = TrendPullbackStrategy()
    bars, feature = strategy._features_for_input([candle], end)
    assert bars[-1].end_time == end
    assert feature.candle_timestamp == end

def _execution_cycle_service(at, *, selected_contract=None, sizing=None):
    service, repo = _service(None)
    service.config.auto_trade_enabled = True
    service.config.mode = AutoTradingMode.PAPER
    service.config.tunables.trend_pullback_enabled = True
    service.config.tunables.volatility_breakout_enabled = False
    service.position_manager.is_within_strategy_a_entry_window = Mock(return_value=True)
    service.position_manager.is_within_entry_window = Mock(return_value=False)
    repo.get_active_trades = AsyncMock(return_value=[])
    repo.list_trades = AsyncMock(return_value=[])
    repo.save_strategy_signal = AsyncMock()
    service._gather_features = AsyncMock(return_value=MarketFeatures(
        timestamp=at,
        spot_price=100,
        futures_price=100,
        data_ready=True,
    ))
    candle = Candle(
        instrument_id="INST-NIFTY-FUT-2026-09-24",
        interval="15m",
        start_time=at - timedelta(minutes=15),
        end_time=at,
        open=99,
        high=101,
        low=98,
        close=100,
        volume=100,
        source="BREEZE",
    )
    service._market_snapshot = ([], [], [candle])
    service.strategy_a = _armed_execution_strategy(at)
    service._get_option_chain = AsyncMock(return_value={"source": "BREEZE", "contracts": []})
    if selected_contract is None:
        service.contract_selector.select_contract = Mock(
            return_value=(None, [], "NO_EXECUTABLE_CONTRACT")
        )
    else:
        service.contract_selector.select_contract = Mock(
            return_value=(selected_contract, [selected_contract], None)
        )
    if sizing is not None:
        service.risk_sizer.size = Mock(return_value=sizing)
    return service, repo


@pytest.mark.asyncio
async def test_real_service_contract_rejection_consumes_trigger_without_phantom_entered():
    at = datetime(2026, 9, 21, 10, 0, tzinfo=IST)
    service, repo = _execution_cycle_service(at)

    result = await service._evaluate_cycle()

    assert result["status"] == "CONTRACT_SELECTION_FAILED"
    assert service.strategy_a.snapshot.state is StrategyState.COOLDOWN
    assert repo.save_trade.await_count == 0
    assert repo.save_runtime.await_count >= 2
    assert repo.save_strategy_signal.await_count == 1


@pytest.mark.asyncio
async def test_real_service_sizing_rejection_consumes_trigger_without_phantom_entered():
    at = datetime(2026, 9, 21, 10, 0, tzinfo=IST)
    contract = SelectedContract(
        instrument_id="OPT-A",
        symbol="NIFTY-OPT-A",
        expiry="2026-09-24",
        strike=100,
        option_type=OptionType.CALL,
        ask_price=10,
        bid_price=9.5,
        open_interest=10000,
        volume=1000,
        spread_pct=0.05,
        lot_size=75,
        ltp=9.75,
        delta=0.62,
        gamma=0.01,
        greek_source="BREEZE",
        quote_timestamp=at,
        quote_freshness_seconds=0,
    )
    sizing = SimpleNamespace(
        lots=0,
        quantity=0,
        risk_budget=1000,
        option_loss_per_lot=1200,
        rejection_reason="RISK_BUDGET_TOO_SMALL",
    )
    service, repo = _execution_cycle_service(at, selected_contract=contract, sizing=sizing)

    result = await service._evaluate_cycle()

    assert result["status"] == "INSUFFICIENT_CAPITAL_OR_RISK_BUDGET"
    assert service.strategy_a.snapshot.state is StrategyState.COOLDOWN
    assert repo.save_trade.await_count == 0
    assert repo.save_runtime.await_count >= 2


@pytest.mark.asyncio
async def test_real_service_success_persists_trade_before_confirming_entered():
    at = datetime(2026, 9, 21, 10, 0, tzinfo=IST)
    contract = SelectedContract(
        instrument_id="OPT-A",
        symbol="NIFTY-OPT-A",
        expiry="2026-09-24",
        strike=100,
        option_type=OptionType.CALL,
        ask_price=10,
        bid_price=9.5,
        open_interest=10000,
        volume=1000,
        spread_pct=0.05,
        lot_size=75,
        ltp=9.75,
        delta=0.62,
        gamma=0.01,
        greek_source="BREEZE",
        quote_timestamp=at,
        quote_freshness_seconds=0,
    )
    sizing = SimpleNamespace(
        lots=1,
        quantity=75,
        risk_budget=1000,
        option_loss_per_lot=500,
        rejection_reason=None,
    )
    service, repo = _execution_cycle_service(at, selected_contract=contract, sizing=sizing)

    result = await service._evaluate_cycle()

    assert result["status"] == "TRADE_OPENED"
    assert repo.save_trade.await_count >= 1
    assert service.strategy_a.snapshot.state is StrategyState.ENTERED
    assert service.strategy_a.last_event.reason == "OPTION_EXECUTION_CONFIRMED"
    assert repo.save_runtime.await_count >= 2



@pytest.mark.asyncio
async def test_gather_features_requests_authoritative_15m_futures_bars():
    service, _ = _service(None)
    future = SimpleNamespace(
        instrument_id="INST-NIFTY-FUT-2026-09-29",
        segment="FUTURES",
        tradable=True,
        expiry="2026-09-29",
    )
    inst_svc = SimpleNamespace(
        upsert_futures_contract=AsyncMock(return_value=future),
        ensure_current_nifty_futures=AsyncMock(return_value=[future]),
        repo=SimpleNamespace(search=AsyncMock(return_value=[future])),
    )
    adapter = SimpleNamespace(
        is_active=True,
        resolve_nearest_future=AsyncMock(return_value={
            "underlying": "NIFTY", "expiry": "2026-09-29",
            "stock_code": "NIFTY", "broker": "ICICI_BREEZE",
            "exchange": "NFO", "lot_size": 1, "tick_size": 0.05,
            "broker_token": "50123",
        }),
        client_manager=SimpleNamespace(is_active=True),
    )
    service.chain_svc = SimpleNamespace(
        inst_svc=inst_svc,
        broker_gateway=SimpleNamespace(
            active_broker_name="breeze", active_adapter=adapter, breeze_adapter=adapter
        ),
    )
    service._get_recent_candles = AsyncMock(return_value=[])
    service._get_option_chain = AsyncMock(return_value={"source": "UNAVAILABLE", "strikes": []})
    await service._gather_features()
    calls = service._get_recent_candles.await_args_list
    assert call("15m", "INST-NIFTY-FUT-2026-09-29") in calls
    assert call("5m", "INST-NIFTY-FUT-2026-09-29") not in calls


def test_shared_feature_engine_preserves_strategy_a_15m_futures():
    at = datetime(2026, 9, 21, 12, 0, tzinfo=IST)
    futures = [
        Candle(
            instrument_id="INST-NIFTY-FUT-2026-09-29", interval="15m",
            start_time=at - timedelta(minutes=15 * (20 - i)),
            end_time=at - timedelta(minutes=15 * (19 - i)),
            open=100+i, high=102+i, low=99+i, close=101+i,
            volume=100, open_interest=1000+i, source="BREEZE",
        )
        for i in range(20)
    ]
    features = __import__("services.strategy.features", fromlist=["FeatureEngine"]).FeatureEngine.compute_all_features(
        [], [], futures_candles=futures, option_chain={}, spot_price=100, as_of=at
    )
    assert features.futures_price == futures[-1].close
    assert features.futures_vwap > 0


def test_strategy_a_diagnostics_expose_stale_futures_instead_of_unavailable():
    end = datetime(2026, 9, 21, 10, 0, tzinfo=IST)
    stale_as_of = datetime(2026, 9, 21, 10, 20, tzinfo=IST)
    candle = Candle(
        instrument_id="INST-NIFTY-FUT-2026-09-29", interval="15m",
        start_time=end - timedelta(minutes=15), end_time=end,
        open=100, high=102, low=98, close=101, volume=100, source="BREEZE",
    )
    strategy = TrendPullbackStrategy()
    diagnostics = strategy.diagnose(
        SimpleNamespace(timestamp=stale_as_of), [], [], futures_candles=[candle]
    )
    assert all(item.key_blocker == "STALE_FUTURES_DATA" for item in diagnostics)


def test_strategy_a_after_force_exit_does_not_require_post_1515_futures_bar():
    end = datetime(2026, 9, 21, 15, 15, tzinfo=IST)
    after_close = datetime(2026, 9, 21, 16, 30, tzinfo=IST)
    candle = Candle(
        instrument_id="INST-NIFTY-FUT-2026-09-29", interval="15m",
        start_time=end - timedelta(minutes=15), end_time=end,
        open=100, high=102, low=98, close=101, volume=100, source="BREEZE",
    )
    strategy = TrendPullbackStrategy()
    bars, feature = strategy._features_for_input([candle], after_close)
    assert bars[-1].end_time == end
    assert feature.candle_timestamp == end


@pytest.mark.asyncio
async def test_strategy_diagnostics_are_cache_only():
    service, repo = _service(None)
    repo.list_trades = AsyncMock(return_value=[])
    service._last_features = MarketFeatures(
        timestamp=datetime(2026, 9, 21, 10, 0, tzinfo=IST),
        spot_price=23400, futures_price=23420,
    )
    service._market_snapshot = ([], [], [])
    service._gather_features = AsyncMock(side_effect=AssertionError("diagnostics must not perform broker I/O"))
    await service.get_trigger_diagnostics()
    service._gather_features.assert_not_awaited()


@pytest.mark.asyncio
async def test_strategy_status_is_cache_only():
    service, repo = _service(None)
    repo.get_active_trades = AsyncMock(return_value=[])
    repo.list_strategy_signals = AsyncMock(return_value=[])
    repo.list_trades = AsyncMock(return_value=[])
    service._last_features = MarketFeatures(
        timestamp=datetime(2026, 9, 21, 10, 0, tzinfo=IST),
        spot_price=23400, futures_price=23420,
    )
    service._gather_features = AsyncMock(side_effect=AssertionError("status must not perform broker I/O"))
    payload = await service.get_status()
    assert payload["features"]["futures_price"] == 23420
    service._gather_features.assert_not_awaited()


@pytest.mark.asyncio
async def test_strategy_a_persists_broker_resolved_future_identity():
    service, _ = _service(None)
    adapter = SimpleNamespace(
        is_active=True,
        resolve_nearest_future=AsyncMock(return_value={
            "underlying": "NIFTY", "expiry": "2026-09-29",
            "stock_code": "NIFTY26SEPFUT", "broker": "ZERODHA_KITE",
            "exchange": "NFO", "lot_size": 65, "tick_size": 0.05, "broker_token": "9001",
        }),
    )
    inst_svc = SimpleNamespace(
        upsert_futures_contract=AsyncMock(return_value=SimpleNamespace(
            instrument_id="INST-NIFTY-FUT-2026-09-29"
        ))
    )
    service.chain_svc = SimpleNamespace(
        inst_svc=inst_svc,
        broker_gateway=SimpleNamespace(active_broker_name="kite", active_adapter=adapter),
    )
    resolved = await service._resolve_strategy_a_futures_instrument()
    assert resolved == "INST-NIFTY-FUT-2026-09-29"
    adapter.resolve_nearest_future.assert_awaited_once_with("NIFTY")
    inst_svc.upsert_futures_contract.assert_awaited_once()


@pytest.mark.asyncio
async def test_strategy_a_does_not_use_calendar_futures_when_breeze_session_is_inactive():
    service, _ = _service(None)
    inst_svc = SimpleNamespace(
        ensure_current_nifty_futures=AsyncMock(side_effect=AssertionError("must not fabricate runtime contract while disconnected")),
        repo=SimpleNamespace(search=AsyncMock(return_value=[])),
    )
    breeze = SimpleNamespace(
        is_active=False,
        client_manager=SimpleNamespace(is_active=False),
        resolve_nearest_future=AsyncMock(return_value=None),
    )
    service.chain_svc = SimpleNamespace(
        inst_svc=inst_svc,
        broker_gateway=SimpleNamespace(
            active_broker_name="breeze",
            active_adapter=breeze,
            breeze_adapter=breeze,
        ),
    )
    resolved = await service._resolve_strategy_a_futures_instrument()
    assert resolved is None
    assert service._market_data_status["last_error"] == "BROKER_SESSION_INACTIVE"
    inst_svc.ensure_current_nifty_futures.assert_not_awaited()


def test_strategy_a_diagnostics_keep_passed_trend_when_confirmation_is_blocker():
    at = datetime(2026, 9, 21, 12, 0, tzinfo=IST)
    strategy = TrendPullbackStrategy()
    feature = __import__("services.strategy.futures_signal", fromlist=["FuturesFeatureSnapshot"]).FuturesFeatureSnapshot(
        contract_id="INST-NIFTY-FUT-2026-09-29",
        candle_timestamp=at,
        candle_start=at - timedelta(minutes=15),
        open=100.0, high=110.0, low=99.0, close=101.0,
        ema20=105.0, ema50=100.0, adx14=30.0, plus_di14=28.0, minus_di14=12.0,
        atr14=10.0, session_vwap=103.0, support=100.0, resistance=120.0, bar_index=20,
    )
    diag = strategy._diagnostic(feature, __import__("services.strategy.strategies.trend_pullback", fromlist=["StrategyDirection"]).StrategyDirection.CALL, "CONFIRMATION_BODY_TOO_WEAK")
    by_id = {item.id: item for item in diag.conditions}
    assert by_id["trend"].status == "PASSED"
    assert by_id["trend"].gap_description == "TREND_CONFIRMED"
    assert by_id["confirmation"].status == "PENDING"
    assert by_id["confirmation"].gap_description == "CONFIRMATION_BODY_TOO_WEAK"
    assert diag.phase_summary["strategy_a_v2"]["trend"]["passed"] is True


def test_strategy_a_diagnostics_surface_structural_risk_rejection():
    at = datetime(2026, 9, 21, 12, 0, tzinfo=IST)
    strategy = TrendPullbackStrategy()
    feature_cls = __import__("services.strategy.futures_signal", fromlist=["FuturesFeatureSnapshot"]).FuturesFeatureSnapshot
    direction_cls = __import__("services.strategy.strategies.trend_pullback", fromlist=["StrategyDirection"]).StrategyDirection
    # Trend, confirmation, and confluence pass, but the structural stop is
    # deliberately far enough away to exceed the configured 1.50 ATR maximum.
    feature = feature_cls(
        contract_id="INST-NIFTY-FUT-2026-09-29",
        candle_timestamp=at,
        candle_start=at - timedelta(minutes=15),
        open=108.0, high=112.0, low=104.0, close=111.0,
        ema20=108.0, ema50=105.0, adx14=30.0, plus_di14=28.0, minus_di14=12.0,
        atr14=4.0, session_vwap=108.5, support=104.0, resistance=130.0, bar_index=20,
    )
    diag = strategy._diagnostic(feature, direction_cls.CALL, "STRUCTURAL_R_ABOVE_MAXIMUM")
    risk = next(item for item in diag.conditions if item.id == "risk")
    assert risk.status == "PENDING"
    assert risk.gap_description == "STRUCTURAL_R_ABOVE_MAXIMUM"
