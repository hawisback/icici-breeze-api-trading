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
    TradeDirection,
)
from services.strategy.replay_lifecycle import (
    HistoricalPositionManagerReplayer,
    _entry_trade,
)
from services.strategy.replay_manifest import ReplayManifestRecorder
from services.strategy.simulation import _record_strategy_b_manifest


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
    _record_strategy_b_manifest(
        recorder,
        signal,
        trading_date="2026-07-01",
        breakout_candle=candle,
    )
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
        _record_strategy_b_manifest(
            recorder,
            _signal(candle.end_time),
            trading_date="2026-07-01",
            breakout_candle=candle,
        )


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
