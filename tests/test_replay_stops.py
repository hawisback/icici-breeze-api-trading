from services.strategy.models import TradeDirection
from services.strategy.replay_stops import evaluate_replay_candle


def test_replay_stop_uses_active_stop_before_completed_thesis_invalidation():
    result = evaluate_replay_candle(
        TradeDirection.BULLISH,
        candle_open=100.0,
        candle_high=102.0,
        candle_low=94.0,
        active_stop=95.0,
        thesis_invalidated_at_close=True,
    )

    assert result.event == "STRUCTURAL_STOP"
    assert result.exit_price == 95.0
    assert not result.ambiguous


def test_replay_stop_preserves_gap_risk():
    result = evaluate_replay_candle(
        "PUT",
        candle_open=106.0,
        candle_high=110.0,
        candle_low=104.0,
        active_stop=105.0,
    )

    assert result.event == "STRUCTURAL_STOP"
    assert result.exit_price == 106.0


def test_entry_candle_crossing_is_ambiguous():
    result = evaluate_replay_candle(
        "CALL",
        candle_open=100.0,
        candle_high=101.0,
        candle_low=94.0,
        active_stop=95.0,
        entry_candle=True,
        thesis_invalidated_at_close=True,
    )

    assert result.event == "AMBIGUOUS_ENTRY_CANDLE"
    assert result.crossed
    assert result.ambiguous
    assert result.exit_price is None


def test_stop_level_is_not_recalculated_from_same_candle():
    result = evaluate_replay_candle(
        "PUT",
        candle_open=100.0,
        candle_high=104.0,
        candle_low=99.0,
        active_stop=105.0,
    )

    assert result.event == "NO_EXIT"
    assert result.stop_level == 105.0
