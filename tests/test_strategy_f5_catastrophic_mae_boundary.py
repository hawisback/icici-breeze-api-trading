from services.historical.strategy_f5_catastrophic_mae_boundary import (
    _candidate_distance,
    _trade_mae,
)
from services.historical.strategy_f5_catastrophic_mae_boundary_protocol import (
    GUARDRAILS,
    PRESERVATION_TARGET_PCT,
    QUANTILE,
)


def test_protocol_derives_one_candidate_without_grid_search():
    assert PRESERVATION_TARGET_PCT == 95.0
    assert QUANTILE == 0.05
    assert GUARDRAILS["no_stop_grid_search"] is True
    assert GUARDRAILS["no_pnl_optimization_on_development"] is True


def test_candidate_uses_farther_successful_trade_p05_boundary():
    result = _candidate_distance(
        [-20.0, -10.0, -5.0, -1.0],
        [-25.0, -8.0, -4.0, -1.0],
    )
    assert result["available"] is True
    assert result["candidate_stop_distance_pct"] > 20.0
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
