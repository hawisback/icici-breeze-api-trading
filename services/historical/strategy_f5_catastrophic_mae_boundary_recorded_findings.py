"""Recorded corrected F5 catastrophic-MAE boundary findings."""

STRATEGY_F5_CATASTROPHIC_MAE_BOUNDARY_RECORDED_FINDINGS_V1 = {
    "research_type": (
        "STRATEGY_F5_CATASTROPHIC_MAE_BOUNDARY_RECORDED_FINDINGS_V1"
    ),
    "protocol_version": "STRATEGY_F5_CATASTROPHIC_MAE_BOUNDARY_V1",
    "strategy_id": "F5",
    "research_only": True,
    "source": {
        "artifact_sha256": (
            "6052a6c0d47da87362c80354309cabb5b3b081c832e54f327546f477878c7a75"
        ),
        "f5_market_sha256": (
            "0ed1b1499505e53c310deddbb2e9a6b78bba48c8c23fab94639dc2fa6765e2a9"
        ),
        "backtest_sha256": (
            "0717c493aff5460d625b588771caa4acb6cc152a1ffc97c0da50cd7d6bf9d0e0"
        ),
        "target_bearish_PE_trades": 84,
        "winners": 29,
        "trail_activated": 34,
    },
    "intrabar_mae": {
        "winners_p05_pct": -21.7993,
        "winners_minimum_pct": -30.5930,
        "nonwinners_p05_pct": -27.9355,
        "nonwinners_minimum_pct": -36.0200,
        "trail_activated_p05_pct": -17.9566,
        "trail_activated_minimum_pct": -30.5930,
    },
    "derived_candidate": {
        "derivation": "EMPIRICAL_95PCT_SUCCESS_PRESERVATION_BOUNDARY",
        "stop_distance_pct": 27.95,
        "stop_return_pct": -27.95,
        "winner_preservation_pct": 96.55,
        "activation_preservation_pct": 97.06,
        "preservation_target_pct": 95.0,
        "not_pnl_optimized": True,
    },
    "reference_only": {
        "zero_observed_success_breach_distance_pct": 30.60,
        "is_candidate": False,
        "meaning": (
            "Distance just beyond the worst observed successful-trade MAE; "
            "reported as a conservative reference only, not a second candidate."
        ),
    },
    "development_trade_touch_at_27_95": {
        "touched_trades": 4,
        "touched_winners": 1,
        "touched_trail_activated": 1,
        "touched_baseline_net_pnl_inr": -2303.91,
        "extreme_losers_touched": 3,
        "recovery_winner_touched": 1,
    },
    "decision": "FREEZE_27_95PCT_CATASTROPHIC_STOP_FOR_FRESH_HOLDOUT",
    "next_step": (
        "Validate exactly the 27.95% pre-activation catastrophic option stop "
        "on untouched March 2026 F5 data. Do not compare nearby stop distances "
        "or promote the 30.60% descriptive reference into a competing candidate."
    ),
    "guardrails": {
        "research_only": True,
        "live_execution": False,
        "paper_execution": False,
        "broker_orders": False,
        "single_frozen_stop_distance_pct": 27.95,
        "no_stop_grid_search": True,
        "no_march_retuning": True,
        "reference_30_60_not_a_candidate": True,
        "existing_f5_entry_exit_trail_frozen": True,
        "strategy_d_remains_paused": True,
    },
}
