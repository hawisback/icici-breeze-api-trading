from services.historical.strategy_f5_nifty_structure_stop import (
    _first_breach_confirmation,
    _resistance_levels,
)
from services.historical.strategy_f5_nifty_structure_stop_protocol import (
    BREACH_RULE,
    GUARDRAILS,
    RESISTANCE_CANDIDATES,
)


def _spot(ts: str, high: float, close: float):
    return {
        "timestamp": ts,
        "date": ts[:10],
        "open": close,
        "high": high,
        "low": close - 1.0,
        "close": close,
    }


def test_resistance_family_is_small_and_frozen():
    assert list(RESISTANCE_CANDIDATES) == [
        "CONFIRMED_SWING_HIGH_2X2",
        "RECENT_30M_HIGH",
        "SESSION_HIGH_TO_ENTRY",
    ]
    assert BREACH_RULE["buffer_points"] == 0.0
    assert BREACH_RULE["required_consecutive_closes"] == 1
    assert GUARDRAILS["no_resistance_buffer_search"] is True


def test_levels_use_only_completed_pre_entry_bars():
    rows = [
        _spot("2026-07-01T09:15:00+05:30", 100.0, 99.0),
        _spot("2026-07-01T09:20:00+05:30", 102.0, 101.0),
        _spot("2026-07-01T09:25:00+05:30", 110.0, 108.0),
        _spot("2026-07-01T09:30:00+05:30", 104.0, 103.0),
        _spot("2026-07-01T09:35:00+05:30", 103.0, 102.0),
        _spot("2026-07-01T09:40:00+05:30", 120.0, 119.0),
    ]
    levels = _resistance_levels(
        rows,
        "2026-07-01T09:45:00+05:30",
    )
    assert levels["CONFIRMED_SWING_HIGH_2X2"] == 110.0
    assert levels["RECENT_30M_HIGH"] == 120.0
    assert levels["SESSION_HIGH_TO_ENTRY"] == 120.0


def test_uncompleted_bar_is_not_used_for_level():
    rows = [
        _spot("2026-07-01T09:15:00+05:30", 100.0, 99.0),
        _spot("2026-07-01T09:20:00+05:30", 101.0, 100.0),
        _spot("2026-07-01T09:25:00+05:30", 150.0, 149.0),
    ]
    levels = _resistance_levels(
        rows,
        "2026-07-01T09:27:00+05:30",
    )
    assert levels["SESSION_HIGH_TO_ENTRY"] == 101.0


def test_breach_requires_completed_close_strictly_above_level():
    rows = [
        _spot("2026-07-01T09:30:00+05:30", 111.0, 110.0),
        _spot("2026-07-01T09:35:00+05:30", 112.0, 111.0),
    ]
    breach = _first_breach_confirmation(
        rows,
        entry_timestamp="2026-07-01T09:30:00+05:30",
        stop_before_timestamp="2026-07-01T09:50:00+05:30",
        level=110.0,
    )
    assert breach == "2026-07-01T09:40:00+05:30"


def test_breach_after_stop_boundary_is_ignored():
    rows = [
        _spot("2026-07-01T09:35:00+05:30", 112.0, 111.0),
    ]
    breach = _first_breach_confirmation(
        rows,
        entry_timestamp="2026-07-01T09:30:00+05:30",
        stop_before_timestamp="2026-07-01T09:40:00+05:30",
        level=110.0,
    )
    assert breach is None
