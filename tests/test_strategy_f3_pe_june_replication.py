from datetime import date

from services.historical.strategy_f3_pe_june_replication_market import (
    _atm_strike,
    _nearest_expiry,
)
from services.historical.strategy_f3_pe_june_replication_protocol import (
    BACKTEST_WINDOW,
    GUARDRAILS,
    MACD,
    OPTION_SELECTION,
    PROTOCOL_VERSION,
    REPLICATION_GATE,
)


def test_pe_june_protocol_is_frozen_and_pe_only():
    assert PROTOCOL_VERSION == "STRATEGY_F3_PE_JUNE_REPLICATION_V1"
    assert BACKTEST_WINDOW["start"] == "2026-06-01"
    assert BACKTEST_WINDOW["end"] == "2026-06-30"
    assert OPTION_SELECTION["rights"] == ["PE"]
    assert MACD["fast_length"] == 12
    assert MACD["slow_length"] == 26
    assert MACD["signal_length"] == 9
    assert MACD["source"] == "option_close"
    assert MACD["minimum_prior_option_bars_before_session"] == 35


def test_pe_june_expiry_schedule_and_atm_rounding():
    assert OPTION_SELECTION["weekly_expiries"] == [
        "2026-06-02",
        "2026-06-09",
        "2026-06-16",
        "2026-06-23",
        "2026-06-30",
        "2026-07-07",
    ]
    assert _nearest_expiry(date(2026, 6, 1)) == date(2026, 6, 2)
    assert _nearest_expiry(date(2026, 6, 30)) == date(2026, 6, 30)
    assert _atm_strike(25024.9) == 25000
    assert _atm_strike(25025.0) == 25050


def test_pe_june_gate_requires_slippage_resilience():
    assert REPLICATION_GATE["net_pnl_after_explicit_costs_positive"] is True
    assert REPLICATION_GATE["profit_factor_above_one"] is True
    assert REPLICATION_GATE[
        "net_pnl_positive_at_0_5_point_slippage_each_side"
    ] is True
    assert REPLICATION_GATE[
        "net_pnl_positive_at_1_0_point_slippage_each_side"
    ] is True
    assert REPLICATION_GATE["full_trade_price_coverage_required"] is True


def test_pe_june_remains_research_only():
    assert GUARDRAILS["pe_only_predeclared_before_june_scoring"] is True
    assert GUARDRAILS["live_execution"] is False
    assert GUARDRAILS["paper_execution"] is False
    assert GUARDRAILS["broker_orders"] is False
    assert GUARDRAILS["no_macd_parameter_optimization"] is True
    assert GUARDRAILS["no_extra_filters"] is True
    assert GUARDRAILS["no_time_of_day_filter"] is True
    assert GUARDRAILS["no_stop_target_search"] is True
