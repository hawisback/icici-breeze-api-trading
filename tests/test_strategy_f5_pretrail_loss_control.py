from services.historical.strategy_f5_pretrail_loss_control_protocol import (
    GUARDRAILS,
    PRESERVATION_SCREEN,
    PROTOCOL_VERSION,
    STOP_CANDIDATES_PCT,
    STOP_EXECUTION,
)
from services.historical.strategy_f5_pretrail_loss_control import (
    _counterfactual_trade,
    _distribution,
)


def test_pretrail_stop_protocol_preserves_frozen_winner_management():
    assert PROTOCOL_VERSION == "STRATEGY_F5_PRETRAIL_LOSS_CONTROL_V1"
    assert STOP_CANDIDATES_PCT == [5.0, 7.5, 10.0, 12.5, 15.0, 20.0]
    assert STOP_EXECUTION["active_only_before_trail_activation"] is True
    assert STOP_EXECUTION["intrabar_low_touch_exit"] is False
    assert STOP_EXECUTION["trail_activation_precedence"] is True
    assert STOP_EXECUTION[
        "after_activation_baseline_f5_management_unchanged"
    ] is True
    assert GUARDRAILS["do_not_change_trail_activation"] is True
    assert GUARDRAILS["do_not_change_trail_distance"] is True


def test_preservation_screen_requires_95pct_of_winners_untouched():
    assert PRESERVATION_SCREEN[
        "minimum_activated_trade_untouched_pct"
    ] == 95.0
    assert PRESERVATION_SCREEN[
        "minimum_baseline_winner_untouched_pct"
    ] == 95.0
    assert PRESERVATION_SCREEN[
        "require_net_pnl_improvement_each_month_zero_slippage"
    ] is True


def _baseline_trade():
    return {
        "entry_timestamp": "2026-09-01T10:00:00+05:30",
        "exit_timestamp": "2026-09-01T10:20:00+05:30",
        "entry_open": 100.0,
        "exit_open": 120.0,
        "expiry": "2026-09-01",
        "strike": 25000,
        "right": "PE",
        "exit_reason": "CLOSE_CONFIRMED_TRAIL10",
    }


def test_stop_triggers_only_on_completed_close_path_before_activation():
    diagnostic = {
        "path": [
            {
                "decision_timestamp": "2026-09-01T10:04:00+05:30",
                "close_return_pct": -3.0,
            },
            {
                "decision_timestamp": "2026-09-01T10:06:00+05:30",
                "close_return_pct": -7.6,
            },
        ]
    }
    next_open = {
        (
            "2026-09-01T10:06:00+05:30",
            "2026-09-01",
            25000,
            "PE",
        ): 92.0
    }

    result = _counterfactual_trade(
        diagnostic,
        7.5,
        next_open,
        _baseline_trade(),
    )

    assert result["stopped_early"] is True
    assert result["exit_timestamp"] == "2026-09-01T10:06:00+05:30"
    assert result["exit_open"] == 92.0
    assert result["trigger_close_return_pct"] == -7.6


def test_wider_stop_leaves_same_trade_untouched():
    diagnostic = {
        "path": [
            {
                "decision_timestamp": "2026-09-01T10:04:00+05:30",
                "close_return_pct": -3.0,
            },
            {
                "decision_timestamp": "2026-09-01T10:06:00+05:30",
                "close_return_pct": -7.6,
            },
        ]
    }

    result = _counterfactual_trade(
        diagnostic,
        10.0,
        {},
        _baseline_trade(),
    )

    assert result["stopped_early"] is False
    assert result["exit_open"] == 120.0
    assert result["exit_reason"] == "CLOSE_CONFIRMED_TRAIL10"


def test_distribution_reports_adverse_excursion_quantiles():
    dist = _distribution([-20.0, -10.0, -5.0, -2.0, 0.0])
    assert dist["p50"] == -5.0
    assert dist["p05"] < dist["p50"]
