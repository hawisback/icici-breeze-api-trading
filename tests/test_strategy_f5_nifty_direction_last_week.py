from services.historical.strategy_f5_nifty_direction_last_week import (
    _alignment,
    _direction,
    _entry_time_direction,
)
from services.historical.strategy_f5_nifty_direction_last_week_protocol import (
    GUARDRAILS,
    PROTOCOL_VERSION,
    WINDOW,
)


def test_direction_protocol_is_last_week_and_diagnostic_only():
    assert PROTOCOL_VERSION == "STRATEGY_F5_NIFTY_DIRECTION_LAST_WEEK_V1"
    assert WINDOW["start"] == "2026-09-21"
    assert WINDOW["end"] == "2026-09-25"
    assert GUARDRAILS["full_day_direction_is_descriptive_only"] is True
    assert GUARDRAILS["entry_time_direction_has_no_lookahead"] is True
    assert GUARDRAILS["no_side_filter_promotion_from_one_week"] is True
    assert GUARDRAILS["live_execution"] is False


def test_direction_and_alignment_mapping():
    assert _direction(0.1) == "BULLISH"
    assert _direction(-0.1) == "BEARISH"
    assert _direction(0.0) == "FLAT"
    assert _alignment("CE", "BULLISH") == "ALIGNED"
    assert _alignment("PE", "BEARISH") == "ALIGNED"
    assert _alignment("CE", "BEARISH") == "COUNTER_DIRECTION"
    assert _alignment("PE", "BULLISH") == "COUNTER_DIRECTION"
    assert _alignment("CE", "FLAT") == "UNCLASSIFIED"


def test_entry_time_direction_uses_only_completed_5m_bar():
    rows = [
        {
            "timestamp": "2026-09-21T09:15:00+05:30",
            "date": "2026-09-21",
            "open": 25000.0,
            "high": 25020.0,
            "low": 24990.0,
            "close": 25010.0,
        },
        {
            "timestamp": "2026-09-21T09:20:00+05:30",
            "date": "2026-09-21",
            "open": 25010.0,
            "high": 25040.0,
            "low": 25005.0,
            "close": 25030.0,
        },
    ]

    before_first_close = _entry_time_direction(
        "2026-09-21T09:19:00+05:30", rows
    )
    assert before_first_close["available"] is False

    at_0921 = _entry_time_direction(
        "2026-09-21T09:21:00+05:30", rows
    )
    assert at_0921["available"] is True
    assert at_0921["latest_completed_bar_start"].startswith(
        "2026-09-21T09:15:00"
    )
    assert at_0921["direction"] == "BULLISH"
    assert at_0921["spot_completed_bar_close"] == 25010.0
