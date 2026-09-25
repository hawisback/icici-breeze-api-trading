from datetime import datetime, timedelta, timezone

import pytest

from libs.contracts.models import Candle
from services.strategy.models import (
    MarketFeatures,
    OptionType,
    RiskConfig,
    SessionTimersConfig,
    StrategyName,
    StrategySignal,
    StrategyTunablesConfig,
    ThresholdOverrides,
    TradeDirection,
)
from services.strategy.replay_execution import ChronologicalReplayExecutor
from services.strategy.replay_lifecycle import (
    HistoricalPositionManagerReplayer,
    _entry_trade,
)
from services.strategy.replay_manifest import ReplayManifestRecorder
from services.strategy.replay_registry import (
    ReplayBarContext,
    ReplaySessionContext,
    ReplayStrategyRegistry,
    VolatilityBreakoutReplayAdapter,
)


UTC = timezone.utc


def _signal(timestamp: datetime) -> StrategySignal:
    return StrategySignal(
        signal_id="SIG-B-TEST",
        strategy=StrategyName.VOLATILITY_BREAKOUT,
        direction=TradeDirection.BULLISH,
        option_type=OptionType.CALL,
        timestamp=timestamp,
        spot_reference_price=101.0,
        structural_stop=99.5,
        r_points=1.5,
        derivatives_score=2.0,
        features_snapshot={
            "box_high": 100.0,
            "box_low": 98.0,
            "atr_at_lock": 2.0,
            "box_created_time": (timestamp - timedelta(minutes=10)).isoformat(),
            "breakout_trigger_price": 100.1,
            "confirmation_score": 3,
            "raw_confirmation_score": 4,
            "effective_confirmation_score": 3,
            "oi_wall_penalty": 1,
            "confirmation_ratio": "3/5",
            "confirmation_factors": {"rvol": True},
            "entry_reference_spot": 101.0,
        },
    )


def _breakout_candle(timestamp: datetime) -> Candle:
    return Candle(
        instrument_id="INDEX",
        interval="5m",
        start_time=timestamp,
        end_time=timestamp + timedelta(minutes=5),
        open=100.5,
        high=101.5,
        low=100.2,
        close=101.0,
        volume=100,
        source="BREEZE",
    )


def _recorded_b_record() -> tuple[ReplayManifestRecorder, object, Candle]:
    recorder = ReplayManifestRecorder()
    candle = _breakout_candle(datetime(2026, 7, 1, 4, 0, tzinfo=UTC))
    signal = _signal(candle.end_time)
    adapter = VolatilityBreakoutReplayAdapter(
        StrategyTunablesConfig(),
        SessionTimersConfig(),
    )
    context = ReplayBarContext(
        session=ReplaySessionContext(
            trading_date="2026-07-01",
            instrument_id="INDEX",
            overrides=ThresholdOverrides(),
            recorder=recorder,
        ),
        bar=candle,
        features=MarketFeatures(spot_price=101.0),
        spot_candles_5m=[candle],
        spot_candles_15m=[],
        futures_candles=[],
    )
    adapter.on_entry_confirmed(signal, context)
    return recorder, recorder.records()[0], candle


def test_strategy_b_manifest_entry_preserves_completed_bar_state_and_deduplicates():
    recorder, record, candle = _recorded_b_record()

    assert len(recorder.records()) == 1
    assert record.strategy_id == StrategyName.VOLATILITY_BREAKOUT.value
    assert record.replay_signal_id == "SIG-B-TEST"
    assert record.direction == "CALL"
    assert record.trigger_source_candle_timestamp == candle.end_time
    assert record.simulated_entry_timestamp == candle.end_time
    assert record.entry_5m_candle_timestamp == candle.start_time
    assert record.entry_occurred_intrabar is False
    assert record.simulated_entry_price == 101.0
    assert record.initial_structural_stop == 99.5
    assert record.box_high == 100.0
    assert record.box_low == 98.0
    assert record.atr_at_lock == 2.0
    assert record.confirmation_available == 5
    assert record.confirmation_passed == 3
    assert record.raw_confirmation_score == 4
    assert record.effective_confirmation_score == 3
    assert record.oi_wall_penalty == 1
    with pytest.raises(ValueError, match="duplicate replay manifest signal"):
        adapter = VolatilityBreakoutReplayAdapter(
            StrategyTunablesConfig(),
            SessionTimersConfig(),
        )
        context = ReplayBarContext(
            session=ReplaySessionContext(
                trading_date="2026-07-01",
                instrument_id="INDEX",
                overrides=ThresholdOverrides(),
                recorder=recorder,
            ),
            bar=candle,
            features=MarketFeatures(spot_price=101.0),
            spot_candles_5m=[candle],
            spot_candles_15m=[],
            futures_candles=[],
        )
        adapter.on_entry_confirmed(_signal(candle.end_time), context)


def test_strategy_b_adapter_rejects_wrong_strategy_and_noncompleted_timestamp():
    recorder = ReplayManifestRecorder()
    candle = _breakout_candle(datetime(2026, 7, 1, 4, 0, tzinfo=UTC))
    adapter = VolatilityBreakoutReplayAdapter(
        StrategyTunablesConfig(),
        SessionTimersConfig(),
    )
    context = ReplayBarContext(
        session=ReplaySessionContext(
            trading_date="2026-07-01",
            instrument_id="INDEX",
            overrides=ThresholdOverrides(),
            recorder=recorder,
        ),
        bar=candle,
        features=MarketFeatures(spot_price=101.0),
        spot_candles_5m=[candle],
        spot_candles_15m=[],
        futures_candles=[],
    )

    wrong_strategy = _signal(candle.end_time).model_copy(
        update={"strategy": StrategyName.TREND_PULLBACK}
    )
    with pytest.raises(ValueError, match="non-Strategy-B"):
        adapter.on_entry_confirmed(wrong_strategy, context)

    wrong_time = _signal(candle.end_time - timedelta(seconds=1))
    with pytest.raises(ValueError, match="completed breakout candle end time"):
        adapter.on_entry_confirmed(wrong_time, context)


def test_chronological_executor_does_not_scan_future_and_blocks_capacity():
    entry_bar = _breakout_candle(datetime(2026, 7, 1, 4, 0, tzinfo=UTC))
    second_bar = entry_bar.model_copy(update={
        "start_time": entry_bar.end_time,
        "end_time": entry_bar.end_time + timedelta(minutes=5),
        "open": 101.0,
        "high": 101.4,
        "low": 100.6,
        "close": 101.2,
    })
    stop_bar = second_bar.model_copy(update={
        "start_time": second_bar.end_time,
        "end_time": second_bar.end_time + timedelta(minutes=5),
        "open": 101.0,
        "high": 101.2,
        "low": 99.0,
        "close": 99.4,
    })
    recorder = ReplayManifestRecorder()
    tunables = StrategyTunablesConfig()
    session = SessionTimersConfig()
    registry = ReplayStrategyRegistry.default(tunables, session)
    replay_session = ReplaySessionContext(
        trading_date="2026-07-01",
        instrument_id="INDEX",
        overrides=ThresholdOverrides(),
        recorder=recorder,
    )
    registry.prepare_session(replay_session)
    context = ReplayBarContext(
        session=replay_session,
        bar=entry_bar,
        features=MarketFeatures(spot_price=101.0),
        spot_candles_5m=[entry_bar],
        spot_candles_15m=[],
        futures_candles=[],
    )
    replayer = HistoricalPositionManagerReplayer(
        risk_config=RiskConfig(max_concurrent_positions=1),
        session_config=session,
        recorder=recorder,
        instrument_id="INDEX",
        warmup_candles=[],
        session_candles=[entry_bar, second_bar, stop_bar],
        futures_candles=[],
    )
    executor = ChronologicalReplayExecutor(
        lifecycle_replayer=replayer,
        registry=registry,
        risk_config=RiskConfig(max_concurrent_positions=1),
    )

    record = executor.accept_signal(_signal(entry_bar.end_time), context)

    # Future stop data already exists in the replay dataset, but accepting the
    # signal must not resolve it ahead of chronological time.
    assert record.lifecycle_status == "PENDING"
    assert executor.state.daily_entries == 1
    assert executor.can_accept_entry() is False

    executor.manage_completed_bar(second_bar, [entry_bar, second_bar])
    assert record.lifecycle_status == "PENDING"
    assert executor.can_accept_entry() is False

    executor.manage_completed_bar(
        stop_bar,
        [entry_bar, second_bar, stop_bar],
    )
    assert record.lifecycle_status == "RESOLVED"
    assert executor.state.completed_positions == 1
    assert executor.can_accept_entry() is True


def test_strategy_b_manifest_hydrates_strategy_specific_active_trade():
    _, record, _ = _recorded_b_record()

    trade = _entry_trade(record, "INDEX")

    assert trade.strategy == StrategyName.VOLATILITY_BREAKOUT
    assert trade.direction == TradeDirection.BULLISH
    assert trade.entry_spot_price == record.simulated_entry_price
    assert trade.initial_structural_stop == record.initial_structural_stop
    assert trade.box_high == 100.0
    assert trade.box_low == 98.0
    assert trade.atr_at_lock == 2.0
    assert trade.consecutive_inside_box_closes == 0


def test_strategy_b_replay_uses_position_manager_false_breakout_behavior():
    recorder, record, entry_bar = _recorded_b_record()
    entry_bar = entry_bar.model_copy(update={"low": 99.0})
    second_bar = entry_bar.model_copy(update={
        "start_time": entry_bar.end_time,
        "end_time": entry_bar.end_time + timedelta(minutes=5),
        "open": 100.0,
        "high": 100.0,
        "low": 99.6,
        "close": 99.7,
    })
    third_bar = second_bar.model_copy(update={
        "start_time": second_bar.end_time,
        "end_time": second_bar.end_time + timedelta(minutes=5),
    })
    replayer = HistoricalPositionManagerReplayer(
        risk_config=RiskConfig(),
        session_config=SessionTimersConfig(),
        recorder=recorder,
        instrument_id="INDEX",
        warmup_candles=[],
        session_candles=[entry_bar, second_bar, third_bar],
        futures_candles=[],
    )
    replayer._features = lambda bar, running: MarketFeatures(
        timestamp=bar.end_time,
        spot_price=bar.close,
        closed_5m_price=bar.close,
        closed_5m_time=bar.end_time,
        atr_5m=2.0,
    )

    replayer.replay_record(record)

    assert record.lifecycle_status == "RESOLVED"
    assert record.exit_reason == "FALSE_BREAKOUT_EXIT"
    assert any(event.event == "FALSE_BREAKOUT_EXIT" for event in record.events)


def test_replay_favorable_event_uses_completed_candle_timestamp_without_intrabar_data():
    recorder, record, entry_bar = _recorded_b_record()
    favorable_bar = entry_bar.model_copy(update={
        "start_time": entry_bar.end_time,
        "end_time": entry_bar.end_time + timedelta(minutes=5),
        "open": 101.0,
        "high": 103.0,
        "low": 100.5,
        "close": 102.0,
    })
    replayer = HistoricalPositionManagerReplayer(
        risk_config=RiskConfig(),
        session_config=SessionTimersConfig(),
        recorder=recorder,
        instrument_id="INDEX",
        warmup_candles=[],
        session_candles=[entry_bar, favorable_bar],
        futures_candles=[],
    )
    replayer._features = lambda bar, running: MarketFeatures(
        timestamp=bar.end_time,
        spot_price=bar.close,
        closed_5m_price=bar.close,
        closed_5m_time=bar.end_time,
        atr_5m=2.0,
    )

    replayer.replay_record(record)

    favorable_event = next(event for event in record.events if event.event == "+1R")
    assert favorable_event.timestamp == favorable_bar.end_time
    assert favorable_event.source_candle == favorable_bar.start_time


def test_strategy_b_replay_rejects_missing_structural_state():
    _, record, _ = _recorded_b_record()
    record.box_high = None

    with pytest.raises(ValueError, match="missing structural state: box_high"):
        _entry_trade(record, "INDEX")


def test_replay_rejects_unknown_strategy_id_instead_of_defaulting_to_strategy_a():
    _, record, _ = _recorded_b_record()
    record.strategy_id = "UNKNOWN_STRATEGY"

    with pytest.raises(ValueError, match="Unsupported replay strategy_id"):
        _entry_trade(record, "INDEX")
