from datetime import UTC, datetime
from types import SimpleNamespace

from services.strategy.replay_intrabar import (
    resolve_active_stop_transition,
    resolve_entry_candle,
    resolve_stop_order,
    resolve_stop_target_order,
)


def bar(minute, *, open_price, high, low, close):
    return SimpleNamespace(
        start_time=datetime(2026, 9, 18, 9, minute, tzinfo=UTC),
        open=open_price,
        high=high,
        low=low,
        close=close,
    )


def test_entry_trigger_then_stop_is_resolved():
    result = resolve_entry_candle(
        "CALL",
        trigger_price=100,
        active_stop=95,
        minute_candles=[
            bar(0, open_price=99, high=101, low=98, close=100),
            bar(1, open_price=100, high=100, low=94, close=95),
        ],
    )
    assert result.event == "ENTRY_THEN_STOP"
    assert result.exit_price == 95


def test_adverse_move_before_put_trigger_is_not_post_entry_stop():
    result = resolve_entry_candle(
        "PUT",
        trigger_price=100,
        active_stop=105,
        minute_candles=[
            bar(0, open_price=104, high=106, low=102, close=105),
            bar(1, open_price=104, high=104, low=99, close=100),
        ],
    )
    assert result.event == "ADVERSE_BEFORE_ENTRY"


def test_same_minute_trigger_and_stop_remains_ambiguous():
    result = resolve_entry_candle(
        "CALL",
        trigger_price=100,
        active_stop=95,
        minute_candles=[bar(0, open_price=98, high=101, low=94, close=99)],
    )
    assert result.event == "STILL_AMBIGUOUS"
    assert result.ambiguous


def test_favorable_threshold_and_stop_same_minute_remains_ambiguous():
    result = resolve_stop_order(
        "CALL",
        active_stop=95,
        favorable_level=105,
        minute_candles=[bar(0, open_price=100, high=106, low=94, close=101)],
    )
    assert result.event == "STILL_AMBIGUOUS"
    assert result.ambiguous



def test_new_trailing_stop_cannot_use_earlier_same_minute_low():
    result = resolve_active_stop_transition(
        "CALL",
        active_stop=95,
        transition_level=105,
        transitioned_stop=100,
        minute_candles=[
            bar(0, open_price=102, high=106, low=99, close=105),
        ],
    )
    assert result.event == "STILL_AMBIGUOUS"
    assert result.ambiguous


def test_new_trailing_stop_is_usable_after_transition_in_prior_minute():
    result = resolve_active_stop_transition(
        "CALL",
        active_stop=95,
        transition_level=105,
        transitioned_stop=100,
        minute_candles=[
            bar(0, open_price=102, high=106, low=101, close=105),
            bar(1, open_price=104, high=104, low=99, close=100),
        ],
    )
    assert result.event == "TRANSITION_THEN_STOP"
    assert result.exit_price == 100


def test_gap_open_proves_transition_before_new_stop_touch():
    result = resolve_active_stop_transition(
        "CALL",
        active_stop=95,
        transition_level=105,
        transitioned_stop=100,
        minute_candles=[
            bar(0, open_price=106, high=107, low=99, close=101),
        ],
    )
    assert result.event == "TRANSITION_THEN_STOP"
    assert not result.ambiguous


def test_same_minute_stop_and_target_remains_ambiguous():
    result = resolve_stop_target_order(
        "CALL",
        active_stop=95,
        target=105,
        minute_candles=[
            bar(0, open_price=100, high=106, low=94, close=101),
        ],
    )
    assert result.event == "STILL_AMBIGUOUS"
    assert result.ambiguous


def test_target_then_stop_across_minutes_resolves_target_first():
    result = resolve_stop_target_order(
        "CALL",
        active_stop=95,
        target=105,
        minute_candles=[
            bar(0, open_price=100, high=106, low=99, close=105),
            bar(1, open_price=104, high=104, low=94, close=95),
        ],
    )
    assert result.event == "TARGET"
    assert result.exit_price == 105
