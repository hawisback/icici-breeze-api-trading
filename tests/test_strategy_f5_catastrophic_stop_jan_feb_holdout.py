from datetime import date

from services.historical.strategy_f5_catastrophic_stop_jan_feb_holdout import (
    _counterfactual_stop,
)
from services.historical.strategy_f5_catastrophic_stop_jan_feb_holdout_market import (
    _nearest_expiry,
)
from services.historical.strategy_f5_catastrophic_stop_jan_feb_holdout_protocol import (
    EXPIRIES,
    FROZEN_STOP_DISTANCE_PCT,
    GUARDRAILS,
    WINDOW,
)


def test_jan_feb_keeps_same_frozen_stop():
    assert FROZEN_STOP_DISTANCE_PCT == 27.95
    assert GUARDRAILS["march_result_did_not_change_stop"] is True
    assert GUARDRAILS["no_jan_feb_stop_retuning"] is True
    assert GUARDRAILS["no_nearby_stop_comparison"] is True


def test_jan_feb_expiries_and_coverage_are_frozen():
    assert EXPIRIES == [
        "2026-01-06",
        "2026-01-13",
        "2026-01-20",
        "2026-01-27",
        "2026-02-03",
        "2026-02-10",
        "2026-02-17",
        "2026-02-24",
        "2026-03-02",
    ]
    assert _nearest_expiry(date(2026, 1, 1)).isoformat() == "2026-01-06"
    assert _nearest_expiry(date(2026, 2, 25)).isoformat() == "2026-03-02"
    assert WINDOW["expected_trading_sessions"] == 41


def _trade():
    return {
        "date": "2026-02-10",
        "entry_timestamp": "2026-02-10T10:00:00+05:30",
        "exit_timestamp": "2026-02-10T10:20:00+05:30",
        "trail_activation_timestamp": None,
        "entry_open": 100.0,
        "exit_open": 90.0,
    }


def test_stop_touch_and_gap_execution_are_unchanged_from_march():
    trade = _trade()
    touch = _counterfactual_stop(
        trade,
        [{
            "timestamp": "2026-02-10T10:02:00+05:30",
            "open": 80.0,
            "low": 70.0,
        }],
    )
    assert touch["stopped"] is True
    assert touch["trigger_type"] == "INTRABAR_LOW_TOUCH"
    assert round(touch["raw_exit_price"], 2) == 72.05

    gap = _counterfactual_stop(
        trade,
        [{
            "timestamp": "2026-02-10T10:02:00+05:30",
            "open": 70.0,
            "low": 68.0,
        }],
    )
    assert gap["stopped"] is True
    assert gap["trigger_type"] == "GAP_THROUGH_OPEN"
    assert gap["raw_exit_price"] == 70.0
