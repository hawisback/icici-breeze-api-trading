from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from libs.contracts.models import Candle
from services.strategy.futures_signal import canonical_active_futures_stream, FuturesFeatureEngine, FuturesFeatureSnapshot
from services.strategy.models import (
    ActiveTrade, AutoTradingMode, MarketFeatures, OptionType, StrategyDirection,
    StrategyName, StrategyState, StrategyStateSnapshot, StrategySetup, StrategyTunablesConfig, TradeDirection, TradeLifecycleState,
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
async def test_strategy_a_structural_stop_is_decided_without_option_quote_and_later_bid_closes_original_reason():
    service, repo = _service(None)
    trade = _trade(lots=1)
    decision_time = datetime(2026, 9, 21, 10, 0, tzinfo=IST)
    await service._evaluate_active_trade(trade, _features(89, decision_time))
    assert trade.pending_exit_reason == "UNDERLYING_STRUCTURAL_STOP"
    assert trade.pending_underlying_exit_time == decision_time
    assert trade.state is not TradeLifecycleState.CLOSED
    assert not repo.save_execution_ledger.await_args_list

    service.mkt_svc.get_latest_quote.return_value = _quote(decision_time + timedelta(minutes=5), 100)
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

    service.mkt_svc.get_latest_quote.return_value = _quote(at + timedelta(minutes=5), 100)
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
