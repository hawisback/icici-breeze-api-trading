from datetime import date

from services.historical.strategy_f3_july_replication_market import _nearest_expiry
from services.historical.strategy_f3_july_replication_protocol import (
    BACKTEST_WINDOW,
    GUARDRAILS,
    MACD,
    OPTION_SELECTION,
    PROTOCOL_VERSION,
    REPLICATION_GATE,
)


def test_full_f3_july_replication_is_unchanged_option_native_macd():
    assert PROTOCOL_VERSION == "STRATEGY_F3_JULY_REPLICATION_V1"
    assert BACKTEST_WINDOW["start"] == "2026-07-01"
    assert BACKTEST_WINDOW["end"] == "2026-07-31"
    assert MACD["fast_length"] == 12
    assert MACD["slow_length"] == 26
    assert MACD["signal_length"] == 9
    assert MACD["source"] == "option_close"
    assert MACD["minimum_prior_option_bars_before_session"] == 35
    assert OPTION_SELECTION["rights"] == ["CE", "PE"]
    assert OPTION_SELECTION["fixed_strike_for_entire_session"] is True


def test_full_f3_july_expiry_schedule():
    assert OPTION_SELECTION["weekly_expiries"] == [
        "2026-07-07",
        "2026-07-14",
        "2026-07-21",
        "2026-07-28",
        "2026-08-04",
    ]
    assert _nearest_expiry(date(2026, 7, 1)) == date(2026, 7, 7)
    assert _nearest_expiry(date(2026, 7, 28)) == date(2026, 7, 28)
    assert _nearest_expiry(date(2026, 7, 31)) == date(2026, 8, 4)


def test_full_f3_july_gate_and_guardrails():
    assert REPLICATION_GATE["net_pnl_after_explicit_costs_positive"] is True
    assert REPLICATION_GATE["profit_factor_above_one"] is True
    assert REPLICATION_GATE[
        "net_pnl_positive_at_0_5_point_slippage_each_side"
    ] is True
    assert GUARDRAILS["no_side_filter"] is True
    assert GUARDRAILS["no_macd_parameter_optimization"] is True
    assert GUARDRAILS["no_extra_filters"] is True
    assert GUARDRAILS["no_time_of_day_filter"] is True
    assert GUARDRAILS["no_stop_target_search"] is True
    assert GUARDRAILS["live_execution"] is False
    assert GUARDRAILS["paper_execution"] is False
