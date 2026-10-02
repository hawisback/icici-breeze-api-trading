from services.historical.strategy_f5_option_support_stop import (
    _first_support_break,
    _support_levels,
)
from services.historical.strategy_f5_option_support_stop_protocol import (
    BREAK_RULE,
    GUARDRAILS,
    SUPPORT_CANDIDATES,
)


def _bar(ts: str, low: float, close: float):
    return {
        "timestamp": ts,
        "date": ts[:10],
        "expiry": "2026-07-07",
        "strike": 24000,
        "right": "PE",
        "open": close,
        "high": close + 1.0,
        "low": low,
        "close": close,
    }


def test_support_family_is_small_and_frozen():
    assert list(SUPPORT_CANDIDATES) == [
        "SIGNAL_BAR_LOW",
        "RECENT_10M_LOW",
        "CONFIRMED_SWING_LOW_2X2",
    ]
    assert BREAK_RULE["buffer_points"] == 0.0
    assert BREAK_RULE["required_consecutive_closes"] == 1
    assert GUARDRAILS["no_support_buffer_search"] is True


def test_signal_and_recent_support_use_only_completed_bars():
    rows = [
        _bar("2026-07-01T09:15:00+05:30", 100.0, 102.0),
        _bar("2026-07-01T09:17:00+05:30", 99.0, 101.0),
        _bar("2026-07-01T09:19:00+05:30", 98.0, 100.0),
        _bar("2026-07-01T09:21:00+05:30", 97.0, 99.0),
        _bar("2026-07-01T09:23:00+05:30", 96.0, 98.0),
        _bar("2026-07-01T09:25:00+05:30", 50.0, 51.0),
    ]
    levels = _support_levels(rows, "2026-07-01T09:25:00+05:30")
    assert levels["SIGNAL_BAR_LOW"] == 96.0
    assert levels["RECENT_10M_LOW"] == 96.0


def test_confirmed_swing_low_requires_two_right_bars():
    rows = [
        _bar("2026-07-01T09:15:00+05:30", 105.0, 106.0),
        _bar("2026-07-01T09:17:00+05:30", 103.0, 104.0),
        _bar("2026-07-01T09:19:00+05:30", 90.0, 92.0),
        _bar("2026-07-01T09:21:00+05:30", 101.0, 102.0),
        _bar("2026-07-01T09:23:00+05:30", 102.0, 103.0),
    ]
    levels = _support_levels(rows, "2026-07-01T09:25:00+05:30")
    assert levels["CONFIRMED_SWING_LOW_2X2"] == 90.0


def test_break_requires_completed_close_strictly_below_support():
    rows = [
        _bar("2026-07-01T09:25:00+05:30", 95.0, 100.0),
        _bar("2026-07-01T09:27:00+05:30", 89.0, 90.0),
    ]
    result = _first_support_break(
        rows,
        entry_timestamp="2026-07-01T09:25:00+05:30",
        stop_before_timestamp="2026-07-01T09:40:00+05:30",
        support=95.0,
    )
    assert result == "2026-07-01T09:29:00+05:30"


def test_break_at_stop_boundary_is_ignored():
    rows = [
        _bar("2026-07-01T09:27:00+05:30", 89.0, 90.0),
    ]
    result = _first_support_break(
        rows,
        entry_timestamp="2026-07-01T09:25:00+05:30",
        stop_before_timestamp="2026-07-01T09:29:00+05:30",
        support=95.0,
    )
    assert result is None
