from services.historical.strategy_f5_catastrophic_stop_march_holdout import (
    _counterfactual_stop,
)
from services.historical.strategy_f5_catastrophic_stop_march_holdout_market import (
    _nearest_expiry,
)
from services.historical.strategy_f5_catastrophic_stop_march_holdout_protocol import (
    EXPIRIES,
    FROZEN_STOP_DISTANCE_PCT,
    GUARDRAILS,
    WINDOW,
)
from datetime import date


def test_march_holdout_freezes_exactly_one_stop():
    assert FROZEN_STOP_DISTANCE_PCT == 27.95
    assert GUARDRAILS["no_march_stop_retuning"] is True
    assert GUARDRAILS["no_nearby_stop_comparison"] is True
    assert GUARDRAILS["no_30_60_candidate"] is True


def test_march_shifted_tuesday_expiries_are_frozen():
    assert EXPIRIES == [
        "2026-03-02",
        "2026-03-10",
        "2026-03-17",
        "2026-03-24",
        "2026-03-30",
    ]
    assert _nearest_expiry(date(2026, 3, 2)).isoformat() == "2026-03-02"
    assert _nearest_expiry(date(2026, 3, 4)).isoformat() == "2026-03-10"
    assert _nearest_expiry(date(2026, 3, 30)).isoformat() == "2026-03-30"
    assert WINDOW["expected_trading_sessions"] == 19


def _trade():
    return {
        "date": "2026-03-10",
        "entry_timestamp": "2026-03-10T10:00:00+05:30",
        "exit_timestamp": "2026-03-10T10:20:00+05:30",
        "trail_activation_timestamp": None,
        "entry_open": 100.0,
        "exit_open": 90.0,
    }


def test_stop_fills_at_stop_on_intrabar_touch():
    trade = _trade()
    rows = [
        {
            "timestamp": "2026-03-10T10:02:00+05:30",
            "open": 80.0,
            "low": 70.0,
        },
    ]
    result = _counterfactual_stop(trade, rows)
    assert result["stopped"] is True
    assert result["trigger_type"] == "INTRABAR_LOW_TOUCH"
    assert round(result["raw_exit_price"], 2) == 72.05


def test_stop_gap_through_fills_at_open():
    trade = _trade()
    rows = [
        {
            "timestamp": "2026-03-10T10:02:00+05:30",
            "open": 70.0,
            "low": 68.0,
        },
    ]
    result = _counterfactual_stop(trade, rows)
    assert result["stopped"] is True
    assert result["trigger_type"] == "GAP_THROUGH_OPEN"
    assert result["raw_exit_price"] == 70.0


def test_activation_timestamp_has_precedence():
    trade = _trade()
    trade["trail_activation_timestamp"] = "2026-03-10T10:04:00+05:30"
    rows = [
        {
            "timestamp": "2026-03-10T10:04:00+05:30",
            "open": 60.0,
            "low": 50.0,
        },
    ]
    result = _counterfactual_stop(trade, rows)
    assert result["stopped"] is False
