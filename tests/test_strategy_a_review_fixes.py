from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from libs.contracts.models import Candle
from services.strategy.contract_selector import ContractSelector, trading_sessions_remaining
from services.strategy.futures_signal import FuturesFeatureSnapshot
from services.strategy.models import (
    ActiveTrade,
    AutoTradingMode,
    MarketFeatures,
    OptionSelectionConfig,
    OptionType,
    RiskConfig,
    SessionTimersConfig,
    StrategyDirection,
    StrategyName,
    StrategySetup,
    StrategySignal,
    StrategyState,
    StrategyStateSnapshot,
    StrategyTunablesConfig,
    TradeDirection,
    TradeLifecycleState,
)
from services.strategy.option_execution_validation import ForwardOptionExecutionValidator
from services.strategy.position_manager import PositionManager
from services.strategy.replay_strategy_a import StrategyAReplayEngine, compare_replay_decisions
from services.strategy.service import StrategyService
from services.strategy.telemetry import StrategyAEvaluationRecord, StrategyATelemetryStore, compare_telemetry_to_replay
from services.strategy.strategies.trend_pullback import TrendPullbackStrategy


UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))


def _features(price: float, at: datetime) -> MarketFeatures:
    return MarketFeatures(
        timestamp=at, spot_price=price, futures_price=price,
        atr_5m=10, atr_15m=10, ema9_5m=price, ema20_5m=price,
        futures_vwap=price, closed_5m_price=price, closed_5m_time=at,
    )


def _trade(direction: TradeDirection, *, lots: int = 1, entry: float = 100.0, risk: float = 10.0) -> ActiveTrade:
    stop = entry - risk if direction is TradeDirection.BULLISH else entry + risk
    return ActiveTrade(
        trade_id=f"T-{direction.value}-{lots}", mode=AutoTradingMode.PAPER,
        strategy=StrategyName.TREND_PULLBACK, direction=direction,
        option_type=OptionType.CALL if direction is TradeDirection.BULLISH else OptionType.PUT,
        contract_symbol="NIFTY-A", contract_instrument_id="OPT-A", expiry="2026-09-24",
        strike=24000, quantity=lots * 75, lot_size=75, lots=lots,
        entry_time=datetime(2026, 9, 21, 9, 45, tzinfo=IST), entry_option_price=100,
        entry_spot_price=entry, initial_structural_stop=stop, initial_r_points=risk,
        current_option_price=100, current_spot_price=entry, current_trailing_stop=stop,
        option_hard_stop_price=0, underlying_entry_price=entry, underlying_current_price=entry,
        underlying_structural_stop=stop, underlying_r=risk, initial_quantity=lots * 75,
        remaining_quantity=lots * 75,
    )


@pytest.mark.parametrize("minute, expected", [(14, False), (15, True), (16, True)])
def test_strategy_a_forced_exit_uses_tunable_1515_boundary(minute, expected):
    pm = PositionManager(strategy_config=StrategyTunablesConfig(forced_exit_time="15:15"))
    at = datetime(2026, 9, 21, 15, minute, tzinfo=IST).astimezone(UTC)
    assert pm.is_strategy_a_force_exit_time(at) is expected


def test_strategy_b_keeps_legacy_1520_force_exit_schedule():
    pm = PositionManager()
    assert pm.is_force_exit_time(datetime(2026, 9, 21, 15, 15, tzinfo=IST)) is False
    assert pm.is_force_exit_time(datetime(2026, 9, 21, 15, 20, tzinfo=IST)) is True
    trade = _trade(TradeDirection.BULLISH).model_copy(update={"strategy": StrategyName.VOLATILITY_BREAKOUT})
    _, reason = pm.update_position(trade, 100, _features(100, datetime(2026, 9, 21, 15, 20, tzinfo=IST)), as_of=datetime(2026, 9, 21, 15, 20, tzinfo=IST))
    assert reason == "SESSION_FORCE_SQUARE_OFF_1520"


@pytest.mark.parametrize("direction, favorable, reversal, expected_stop, exit_price", [
    (TradeDirection.BULLISH, 111, 101, 102, 101),
    (TradeDirection.BEARISH, 89, 99, 98, 99),
])
def test_strategy_a_protective_stop_is_enforced_and_monotonic(direction, favorable, reversal, expected_stop, exit_price):
    pm = PositionManager()
    trade = _trade(direction)
    at = datetime(2026, 9, 21, 10, 0, tzinfo=IST)
    updated, reason = pm.update_position(trade, 100, _features(favorable, at), as_of=at)
    assert reason is None
    assert updated.current_trailing_stop == expected_stop
    updated, reason = pm.update_position(updated, 100, _features(reversal, at + timedelta(minutes=5)), as_of=at + timedelta(minutes=5))
    assert reason == "UNDERLYING_TRAILING_STOP"
    assert updated.current_trailing_stop == expected_stop


@pytest.mark.parametrize("lots, expected_partial, expected_remaining", [(1, 0, 75), (2, 75, 75), (3, 75, 150), (4, 150, 150)])
def test_strategy_a_t1_partial_exit_is_deterministic_whole_lots(lots, expected_partial, expected_remaining):
    pm = PositionManager()
    trade = _trade(TradeDirection.BULLISH, lots=lots)
    at = datetime(2026, 9, 21, 10, 0, tzinfo=IST)
    updated, reason = pm.update_position(trade, 100, _features(115, at), as_of=at)
    expected_reason = "T1_PARTIAL_EXIT" if expected_partial else "T1_REACHED_NO_PARTIAL_ONE_LOT"
    assert reason == expected_reason
    assert updated.t1_reached is True
    assert updated.state is TradeLifecycleState.PROFIT_LOCKED
    assert updated.t1_exit_quantity == expected_partial
    assert updated.remaining_quantity == lots * updated.lot_size
    assert updated.quantity == lots * 75  # quantity changes only after an executable fill
    if expected_partial:
        pm.apply_t1_partial_fill(updated, raw_bid=100, executable_price=99, slippage_points=1, filled_at=at)
        assert updated.quantity == expected_remaining
    updated, repeated = pm.update_position(updated, 100, _features(115, at + timedelta(minutes=5)), as_of=at + timedelta(minutes=5))
    assert repeated is None
    assert updated.t1_exit_quantity == expected_partial


@pytest.mark.parametrize("direction, entry", [(StrategyDirection.CALL, 105.0), (StrategyDirection.PUT, 95.0)])
def test_strategy_signal_uses_actual_futures_trigger_or_gap_entry_for_r(direction, entry):
    strategy = TrendPullbackStrategy()
    ts = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    setup = StrategySetup(
        direction=direction, setup_timestamp=ts, confirmation_bar_timestamp=ts,
        confirmation_high=110, confirmation_low=90, trigger_price=100,
        structural_stop=90 if direction is StrategyDirection.CALL else 110,
        initial_underlying_r=10, relevant_support_resistance_level=95,
        confluence_references=("CONFIRMED_SR",), setup_expiry_timestamp=ts + timedelta(minutes=30),
        setup_expiry_bar_index=2,
    )
    feature = FuturesFeatureSnapshot(
        contract_id="INST-NIFTY-FUT-2026-09-24", candle_timestamp=ts, candle_start=ts - timedelta(minutes=15),
        open=entry, high=entry + 2, low=entry - 2, close=100, ema20=105, ema50=100,
        adx14=30, plus_di14=30, minus_di14=10, atr14=10, session_vwap=100, bar_index=10,
    )
    signal = strategy._signal(setup, feature, entry)
    assert signal.underlying_entry_price == entry
    assert signal.r_points == abs(entry - setup.structural_stop)
    assert signal.features_snapshot["underlying_entry_price"] == entry


def test_strategy_rollover_resets_runtime_state_with_explicit_reason():
    strategy = TrendPullbackStrategy()
    old_setup = StrategySetup(
        direction=StrategyDirection.CALL, setup_timestamp=datetime(2026, 9, 21, 10, 0, tzinfo=UTC),
        confirmation_bar_timestamp=datetime(2026, 9, 21, 10, 0, tzinfo=UTC), confirmation_high=110,
        confirmation_low=100, trigger_price=110, structural_stop=100, initial_underlying_r=10,
        relevant_support_resistance_level=100, confluence_references=("SR",),
        setup_expiry_timestamp=datetime(2026, 9, 21, 10, 30, tzinfo=UTC), setup_expiry_bar_index=2,
    )
    strategy.snapshot = StrategyStateSnapshot().transition(StrategyState.SETUP, setup=old_setup)
    strategy.active_contract_id = "INST-NIFTY-FUT-2026-09-24"
    new = Candle(instrument_id="INST-NIFTY-FUT-2026-10-01", interval="15m", start_time=datetime(2026, 9, 21, 10, 15, tzinfo=UTC), end_time=datetime(2026, 9, 21, 10, 30, tzinfo=UTC), open=100, high=102, low=98, close=101, volume=100, source="BREEZE")
    strategy.evaluate(_features(101, new.end_time), [], [], futures_candles=[new])
    assert strategy.snapshot.state is StrategyState.FLAT
    assert strategy.last_event.reason == "FUTURES_ROLLOVER_RESET"
    assert strategy.last_event.details["previous_contract"] == "INST-NIFTY-FUT-2026-09-24"


def _chain(as_of: datetime, *, delta=0.625, quote_timestamp=None, bid=100, ask=101, expiry="2026-09-24"):
    return {"strikes": [{"strike": 24000, "call": {
        "expiry": expiry, "instrument_id": "OPT-1", "bid": bid, "ask": ask,
        "delta": delta, "delta_source": "BROKER", "open_interest": 20000,
        "volume": 1000, "lot_size": 75, "quote_timestamp": quote_timestamp or as_of.isoformat(),
    }}]}


@pytest.mark.parametrize("mutator, status", [
    (lambda chain, at: chain["strikes"][0]["call"].update(quote_timestamp=None), "REJECTED_MISSING_QUOTE_TIMESTAMP"),
    (lambda chain, at: chain["strikes"][0]["call"].update(quote_timestamp="2026-09-21T10:00:00"), "REJECTED_INVALID_QUOTE_TIMESTAMP"),
    (lambda chain, at: chain["strikes"][0]["call"].update(quote_timestamp=(at + timedelta(seconds=1)).isoformat()), "REJECTED_FUTURE_QUOTE_TIMESTAMP"),
    (lambda chain, at: chain["strikes"][0]["call"].update(quote_timestamp=(at - timedelta(minutes=2)).isoformat()), "REJECTED_STALE_QUOTE"),
])
def test_strategy_a_rejects_missing_naive_future_and_stale_quotes(mutator, status):
    at = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    chain = _chain(at)
    mutator(chain, at)
    _, inspected, _ = ContractSelector().select_contract(TradeDirection.BULLISH, underlying_price=24000, option_chain=chain, as_of=at)
    assert inspected[0]["status"] == status


def test_preferred_delta_band_is_ranked_before_outside_band_quality():
    at = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    chain = {"strikes": [
        {"strike": 23900, "call": {**_chain(at)["strikes"][0]["call"], "instrument_id": "OUT", "delta": 0.59, "open_interest": 999999}},
        {"strike": 24000, "call": {**_chain(at)["strikes"][0]["call"], "instrument_id": "IN", "delta": 0.64, "open_interest": 20000}},
    ]}
    selected, _, reason = ContractSelector().select_contract(TradeDirection.BULLISH, underlying_price=24000, option_chain=chain, as_of=at)
    assert reason is None
    assert selected.instrument_id == "IN"
    assert selected.selection_metadata["ranking"].startswith("preferred_band")


def test_expiry_holiday_configuration_changes_session_eligibility():
    as_of = date(2026, 9, 17)
    expiry = date(2026, 9, 21)
    assert trading_sessions_remaining(as_of, expiry) == 2
    assert trading_sessions_remaining(as_of, expiry, {date(2026, 9, 18)}) == 1
    at = datetime(2026, 9, 17, 10, 0, tzinfo=UTC)
    selected, _, reason = ContractSelector(OptionSelectionConfig(exchange_holidays=(date(2026, 9, 18),))).select_contract(TradeDirection.BULLISH, underlying_price=24000, option_chain=_chain(at, expiry="2026-09-21"), as_of=at)
    assert selected is None
    assert reason == "NO_ELIGIBLE_EXPIRY_TWO_TRADING_SESSIONS"


def test_phase9_rejection_accounting_uses_inspected_status_buckets():
    at = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    signal = StrategySignal(signal_id="S-P9", strategy=StrategyName.TREND_PULLBACK, direction=TradeDirection.BULLISH, option_type=OptionType.CALL, timestamp=at, spot_reference_price=24000, underlying_entry_price=24020, structural_stop=24000, r_points=20, derivatives_score=0)
    chains = {
        at.isoformat(): _chain(at, expiry="2026-09-21"),
    }
    report = ForwardOptionExecutionValidator().validate([signal], chains)
    assert report.expiry_rejection_count == 1
    assert report.results[0].state == "UNDERLYING_VALID"


@pytest.mark.parametrize("chain, field", [
    (_chain(datetime(2026, 9, 21, 10, 0, tzinfo=UTC), expiry="2026-09-21"), "expiry_rejection_count"),
    (_chain(datetime(2026, 9, 21, 10, 0, tzinfo=UTC), delta=0.20), "delta_rejection_count"),
    (_chain(datetime(2026, 9, 21, 10, 0, tzinfo=UTC), quote_timestamp="2026-09-21T09:00:00+00:00"), "stale_quote_count"),
    (_chain(datetime(2026, 9, 21, 10, 0, tzinfo=UTC), bid=100, ask=110), "spread_liquidity_rejection_count"),
    (_chain(datetime(2026, 9, 21, 10, 0, tzinfo=UTC)), "sizing_rejection_count"),
])
def test_phase9_rejection_buckets_are_mutually_accounted(chain, field):
    at = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    if field == "sizing_rejection_count":
        # The selector remains executable; only structural-risk sizing rejects.
        chain["strikes"][0]["call"]["ask"] = 100
        validator = ForwardOptionExecutionValidator(risk_config=RiskConfig(max_trade_capital=5000))
    else:
        validator = ForwardOptionExecutionValidator()
    signal = StrategySignal(signal_id=f"S-{field}", strategy=StrategyName.TREND_PULLBACK, direction=TradeDirection.BULLISH, option_type=OptionType.CALL, timestamp=at, spot_reference_price=24000, underlying_entry_price=24020, structural_stop=24000, r_points=20, derivatives_score=0)
    report = validator.validate([signal], {at.isoformat(): chain})
    assert getattr(report, field) == 1
    assert sum((report.expiry_rejection_count, report.delta_rejection_count, report.stale_quote_count, report.spread_liquidity_rejection_count, report.sizing_rejection_count)) == 1


def test_phase9_requires_authoritative_underlying_entry_for_strategy_a():
    at = datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    signal = StrategySignal(signal_id="S-MISSING-ENTRY", strategy=StrategyName.TREND_PULLBACK, direction=TradeDirection.BULLISH, option_type=OptionType.CALL, timestamp=at, spot_reference_price=24000, structural_stop=23990, r_points=10, derivatives_score=0)
    report = ForwardOptionExecutionValidator().validate([signal], {at.isoformat(): _chain(at)})
    assert report.results[0].reason == "MISSING_UNDERLYING_ENTRY_PRICE"
    assert report.executable_coverage == 0


def _feature_snapshot(**updates):
    base = dict(contract_id="FUT", candle_timestamp=datetime(2026, 9, 21, 10, 0, tzinfo=UTC), candle_start=datetime(2026, 9, 21, 9, 45, tzinfo=UTC), open=100, high=110, low=90, close=108, ema20=105, ema50=100, adx14=30, plus_di14=30, minus_di14=10, atr14=10, session_vwap=106, support=100, resistance=130, bar_index=10)
    base.update(updates)
    return FuturesFeatureSnapshot(**base)


@pytest.mark.parametrize("direction, updates, expected", [
    (StrategyDirection.CALL, {}, True),
    (StrategyDirection.PUT, {"ema20": 95, "ema50": 100, "plus_di14": 10, "minus_di14": 30, "close": 92, "open": 100}, True),
    (StrategyDirection.CALL, {"adx14": 21}, False),
    (StrategyDirection.CALL, {"ema20": 100.5, "ema50": 100, "atr14": 10}, False),
])
def test_phase8_trend_regime_matrix(direction, updates, expected):
    assert TrendPullbackStrategy()._trend_ok(_feature_snapshot(**updates), direction)[0] is expected


@pytest.mark.parametrize("direction, updates, expected_reason", [
    (StrategyDirection.CALL, {"close": 109, "open": 108}, "CONFIRMATION_BODY_TOO_WEAK"),
    (StrategyDirection.CALL, {"close": 91, "open": 100}, "CONFIRMATION_DIRECTION_MISMATCH"),
    (StrategyDirection.PUT, {"close": 109, "open": 100}, "CONFIRMATION_DIRECTION_MISMATCH"),
])
def test_phase8_confirmation_matrix(direction, updates, expected_reason):
    ok, reason = TrendPullbackStrategy()._confirmation_ok(_feature_snapshot(**updates), direction)
    assert not ok
    assert reason == expected_reason


def test_phase8_confluence_and_structural_stop_use_confirmed_extremes_and_room():
    strategy = TrendPullbackStrategy()
    feature = _feature_snapshot(support=99, resistance=140, low=100, high=110, close=105, session_vwap=104)
    ok, refs, level, reason = strategy._confluence(feature, StrategyDirection.CALL)
    assert ok and level == 99 and "CONFIRMED_SR" in refs and reason == "CONFLUENCE_CONFIRMED"
    setup, rejection = strategy._build_setup(feature, StrategyDirection.CALL, refs, level)
    assert rejection is None
    assert setup.structural_stop < min(feature.low, feature.support)
    assert setup.initial_underlying_r == abs(setup.trigger_price - setup.structural_stop)


def test_strategy_a_session_boundaries_are_explicit_and_strategy_b_remains_separate():
    pm = PositionManager(strategy_config=StrategyTunablesConfig())
    assert pm.is_within_strategy_a_entry_window(datetime(2026, 9, 21, 9, 44, tzinfo=IST)) is False
    assert pm.is_within_strategy_a_entry_window(datetime(2026, 9, 21, 9, 45, tzinfo=IST)) is True
    assert pm.is_within_strategy_a_entry_window(datetime(2026, 9, 21, 14, 45, tzinfo=IST)) is True
    assert pm.is_within_strategy_a_entry_window(datetime(2026, 9, 21, 14, 46, tzinfo=IST)) is False


def test_genuine_runtime_vs_replay_decision_parity_uses_separate_strategy_instances():
    start = datetime(2026, 9, 21, 9, 15, tzinfo=UTC)
    bars = [Candle(instrument_id="INST-NIFTY-FUT-2026-09-24", interval="15m", start_time=start + timedelta(minutes=15*i), end_time=start + timedelta(minutes=15*(i+1)), open=100+i, high=102+i, low=98+i, close=101+i, volume=100, source="BREEZE") for i in range(55)]
    report = StrategyAReplayEngine().replay(bars)
    runtime = TrendPullbackStrategy()
    history = []
    runtime_rows = []
    for bar in bars:
        history.append(bar)
        event_clock = SimpleNamespace(timestamp=bar.end_time)
        signal = runtime.evaluate(event_clock, [], [], futures_candles=history)
        setup = runtime.snapshot.setup
        event = runtime.last_event
        runtime_rows.append({"timestamp": bar.end_time.isoformat(), "futures_contract": bar.instrument_id, "state": runtime.snapshot.state.value, "event": event.event if event else None, "reason": event.reason if event else None, "signal_id": signal.signal_id if signal else None, "direction": signal.option_type.value if signal else (setup.direction.value if setup else None), "trigger": setup.trigger_price if setup else None, "stop": setup.structural_stop if setup else None, "r_points": signal.r_points if signal else (setup.initial_underlying_r if setup else None), "underlying_entry_price": signal.underlying_entry_price if signal else None})
    assert compare_replay_decisions(runtime_rows, report.decisions) == []


def test_telemetry_comparison_is_exact_and_restart_persistence_shape_is_structured():
    row = StrategyAEvaluationRecord(timestamp="2026-09-21T10:00:00+00:00", futures_contract="FUT", completed_candle_timestamp="2026-09-21T09:45:00+00:00", strategy_state="SETUP", trigger=101, structural_stop=95, underlying_r=6, management_event="SETUP_CREATED")
    store = StrategyATelemetryStore()
    store.append(row)
    assert store.summary()["setups"] == 1
    assert compare_telemetry_to_replay([row], [row]) == []
    changed = row.model_copy(update={"trigger": 102})
    assert compare_telemetry_to_replay([row], [changed])[0]["fields"]["trigger"] == (101.0, 102.0)


@pytest.mark.asyncio
async def test_strategy_a_telemetry_persists_and_restores_across_service_restart(tmp_path):
    from services.strategy.repository import StrategyRepository

    repository = StrategyRepository(tmp_path / "telemetry.db")
    await repository.initialize()
    first = StrategyService(oms_service=SimpleNamespace(), repository=repository)
    row = StrategyAEvaluationRecord(timestamp="2026-09-21T10:00:00+00:00", futures_contract="FUT", completed_candle_timestamp="2026-09-21T09:45:00+00:00", strategy_state="SETUP", management_event="SETUP_CREATED")
    await first._persist_strategy_a_telemetry(row)
    restarted = StrategyService(oms_service=SimpleNamespace(), repository=repository)
    await restarted.initialize()
    assert restarted.get_strategy_a_telemetry_summary()["evaluations"] == 1
    assert (await repository.list_decision_logs(limit=10))[0].category == "STRATEGY_A_TELEMETRY"


def test_live_safety_has_no_strategy_a_order_path_in_review_fixes():
    service = StrategyService(oms_service=SimpleNamespace())
    assert service.config.mode is AutoTradingMode.PAPER
    assert service.config.system_armed is False
    assert service._live_orders_enabled() is False
