from services.historical.strategy_f5_catastrophic_mae_boundary import (
    _candidate_distance,
    _trade_mae,
)
from services.historical.strategy_f5_catastrophic_mae_boundary_protocol import (
    GUARDRAILS,
    OPERATIONAL_ROUNDING_STEP_PCT,
    PRESERVATION_TARGET_PCT,
    QUANTILE,
)


def test_protocol_derives_one_candidate_without_grid_search():
    assert PRESERVATION_TARGET_PCT == 95.0
    assert QUANTILE == 0.05
    assert OPERATIONAL_ROUNDING_STEP_PCT == 0.01
    assert GUARDRAILS["no_stop_grid_search"] is True
    assert GUARDRAILS["no_pnl_optimization_on_development"] is True


def test_candidate_uses_empirical_preservation_not_interpolated_p05():
    winners = [
        -30.592992, -27.947598, -12.576897, -12.174767, -11.624745,
        -6.226415, -4.9132, -4.243743, -3.692762, -3.189433,
        -2.991773, -2.94665, -2.388664, -2.192493, -2.095935,
        -2.079598, -1.975052, -1.962293, -1.728248, -1.367941,
        -1.149425, -0.734574, -0.678295, -0.331638, -0.158856,
        -3.0, -4.0, -5.0, -6.0,
    ]
    activated = winners + [-4.334484, -5.042017, -0.772201, -4.262948, -2.778821]
    result = _candidate_distance(winners, activated)
    assert result["available"] is True
    assert result["candidate_stop_distance_pct"] == 27.95
    assert result["development_winner_preservation_pct_at_boundary"] >= 95.0
    assert result["development_activation_preservation_pct_at_boundary"] >= 95.0
    assert result["zero_observed_success_breach_reference_pct"] == 30.6
    assert result["zero_success_breach_reference_is_candidate"] is False
    assert result["not_pnl_optimized"] is True


def test_trade_mae_uses_intrabar_low_before_activation():
    trade = {
        "date": "2026-07-01",
        "month": "2026-07",
        "entry_timestamp": "2026-07-01T09:30:00+05:30",
        "exit_timestamp": "2026-07-01T09:40:00+05:30",
        "trail_activation_timestamp": "2026-07-01T09:34:00+05:30",
        "trail_activated": True,
        "expiry": "2026-07-07",
        "strike": 24000,
        "right": "PE",
        "entry_open": 100.0,
        "primary_cost_model": {"net_pnl_inr": 500.0},
    }
    rows = [
        {
            "date": "2026-07-01",
            "timestamp": "2026-07-01T09:30:00+05:30",
            "expiry": "2026-07-07",
            "strike": 24000,
            "right": "PE",
            "low": 90.0,
            "high": 105.0,
        },
        {
            "date": "2026-07-01",
            "timestamp": "2026-07-01T09:32:00+05:30",
            "expiry": "2026-07-07",
            "strike": 24000,
            "right": "PE",
            "low": 80.0,
            "high": 120.0,
        },
        {
            "date": "2026-07-01",
            "timestamp": "2026-07-01T09:34:00+05:30",
            "expiry": "2026-07-07",
            "strike": 24000,
            "right": "PE",
            "low": 50.0,
            "high": 130.0,
        },
    ]
    result = _trade_mae(rows, trade)
    assert result["pretrail_1m_bar_count"] == 2
    assert result["pretrail_mae_low_return_pct"] == -20.0
    assert result["pretrail_mfe_high_return_pct"] == 20.0
