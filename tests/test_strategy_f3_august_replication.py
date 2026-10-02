from datetime import date

from services.historical.strategy_f3_august_replication_market import _nearest_expiry
from services.historical.strategy_f3_august_replication_protocol import (
    BACKTEST_WINDOW,
    GUARDRAILS,
    MACD,
    OPTION_SELECTION,
    PROTOCOL_VERSION,
)


def test_f3_august_replication_is_unchanged_option_native_macd():
    assert PROTOCOL_VERSION == "STRATEGY_F3_AUGUST_REPLICATION_V1"
    assert BACKTEST_WINDOW["start"] == "2026-08-01"
    assert BACKTEST_WINDOW["end"] == "2026-08-31"
    assert MACD["fast_length"] == 12
    assert MACD["slow_length"] == 26
    assert MACD["signal_length"] == 9
    assert MACD["source"] == "option_close"
    assert MACD["minimum_prior_option_bars_before_session"] == 35
    assert OPTION_SELECTION["fixed_strike_for_entire_session"] is True
    assert GUARDRAILS["no_pe_only_execution_filter"] is True
    assert GUARDRAILS["no_macd_parameter_optimization"] is True
    assert GUARDRAILS["no_extra_filters"] is True
    assert GUARDRAILS["no_time_of_day_filter"] is True


def test_f3_august_weekly_expiry_schedule():
    assert OPTION_SELECTION["weekly_expiries"] == [
        "2026-08-04",
        "2026-08-11",
        "2026-08-18",
        "2026-08-25",
        "2026-09-01",
    ]
    assert _nearest_expiry(date(2026, 8, 3)) == date(2026, 8, 4)
    assert _nearest_expiry(date(2026, 8, 25)) == date(2026, 8, 25)
    assert _nearest_expiry(date(2026, 8, 31)) == date(2026, 9, 1)
