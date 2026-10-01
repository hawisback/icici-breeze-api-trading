from datetime import date

import pytest

from services.historical.strategy_f_macd_options_backtest import (
    _trade_intents,
    _transaction_costs,
)
from services.historical.strategy_f_macd_options_market import (
    _atm_strike,
    _nearest_expiry,
)
from services.historical.strategy_f_macd_options_protocol import (
    BACKTEST_WINDOW,
    COST_MODEL,
    EXECUTION,
    GUARDRAILS,
    MACD,
    OPTION_SELECTION,
    PROTOCOL_VERSION,
    STRATEGY_ID,
    STRATEGY_NAME,
)


def _event(ts, direction, close, strike, expiry="2026-09-08"):
    return {
        "date": ts[:10],
        "signal_bar_start": "2026-09-03T09:55:00+05:30",
        "signal_bar_end": ts,
        "event_timestamp": ts,
        "direction": direction,
        "right": "CE" if direction == "BULLISH" else "PE",
        "signal_close": close,
        "macd": 1.0 if direction == "BULLISH" else -1.0,
        "macd_signal": 0.0,
        "expiry": expiry,
        "strike": strike,
    }


def test_strategy_f_protocol_is_backtest_only_and_macd_is_exact():
    assert PROTOCOL_VERSION == "STRATEGY_F_MACD_OPTIONS_V1"
    assert STRATEGY_ID == "F"
    assert STRATEGY_NAME == "MACD_CALL_PUT_CROSSOVER"
    assert BACKTEST_WINDOW["start"] == "2026-09-01"
    assert BACKTEST_WINDOW["end"] == "2026-09-30"
    assert MACD == {
        "fast_length": 12,
        "slow_length": 26,
        "signal_length": 9,
        "source": "close",
        "ema_adjust": False,
        "continuous_across_sessions": True,
    }
    assert GUARDRAILS["backtest_only"] is True
    assert GUARDRAILS["live_execution"] is False
    assert GUARDRAILS["paper_execution"] is False
    assert GUARDRAILS["broker_orders"] is False
    assert GUARDRAILS["no_parameter_optimization"] is True


@pytest.mark.parametrize(
    ("price", "expected"),
    [
        (25024.9, 25000),
        (25025.0, 25050),
        (25049.9, 25050),
        (25075.0, 25100),
    ],
)
def test_strategy_f_atm_rounding_is_half_up(price, expected):
    assert _atm_strike(price) == expected


def test_strategy_f_nearest_weekly_expiry_includes_zero_dte():
    assert _nearest_expiry(date(2026, 9, 1)) == date(2026, 9, 1)
    assert _nearest_expiry(date(2026, 9, 2)) == date(2026, 9, 8)
    assert _nearest_expiry(date(2026, 9, 30)) == date(2026, 10, 6)


def test_strategy_f_reverses_on_opposite_crossover_and_forces_flat():
    events = [
        _event("2026-09-03T10:00:00+05:30", "BULLISH", 25010.0, 25000),
        _event("2026-09-03T11:15:00+05:30", "BEARISH", 24970.0, 24950),
        _event("2026-09-03T13:05:00+05:30", "BULLISH", 25030.0, 25050),
    ]

    intents = _trade_intents(events)

    assert len(intents) == 3
    assert intents[0]["right"] == "CE"
    assert intents[0]["entry_timestamp"] == "2026-09-03T10:00:00+05:30"
    assert intents[0]["exit_timestamp"] == "2026-09-03T11:15:00+05:30"
    assert intents[0]["exit_reason"] == "OPPOSITE_MACD_CROSSOVER"

    assert intents[1]["right"] == "PE"
    assert intents[1]["entry_timestamp"] == "2026-09-03T11:15:00+05:30"
    assert intents[1]["exit_timestamp"] == "2026-09-03T13:05:00+05:30"

    assert intents[2]["right"] == "CE"
    assert intents[2]["exit_timestamp"] == "2026-09-03T15:20:00+05:30"
    assert intents[2]["exit_reason"] == "FORCE_EXIT_15_20"


def test_strategy_f_does_not_reverse_at_force_exit():
    events = [
        _event("2026-09-03T14:30:00+05:30", "BULLISH", 25010.0, 25000),
        _event("2026-09-03T15:20:00+05:30", "BEARISH", 24980.0, 25000),
    ]
    intents = _trade_intents(events)
    assert len(intents) == 1
    assert intents[0]["right"] == "CE"
    assert intents[0]["exit_timestamp"] == "2026-09-03T15:20:00+05:30"


def test_strategy_f_cost_model_uses_one_65_unit_lot_and_explicit_fees():
    result = _transaction_costs(
        100.0,
        110.0,
        slippage_points=0.0,
    )
    assert OPTION_SELECTION["lot_size"] == 65
    assert result["entry_fill"] == 100.0
    assert result["exit_fill"] == 110.0
    assert result["gross_pnl_inr"] == 650.0
    assert result["brokerage_inr"] == 40.0
    assert result["explicit_costs_inr"] > 40.0
    assert result["net_pnl_inr"] < result["gross_pnl_inr"]


def test_strategy_f_slippage_sensitivity_is_frozen_not_optimized():
    assert COST_MODEL["primary_slippage_points_each_side"] == 0.0
    assert COST_MODEL["slippage_sensitivity_points_each_side"] == [0.0, 0.5, 1.0]
    assert EXECUTION["stop_loss"] is None
    assert EXECUTION["profit_target"] is None
