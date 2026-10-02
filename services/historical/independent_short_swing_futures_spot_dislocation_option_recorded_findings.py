"""Recorded exact-option rejection for futures-spot return-dislocation reversion.

The futures structural screen passed broadly, but the separately frozen exact
long-option implementation failed before blind validation. No post-hoc rescue is
permitted and no blind data were consumed.
"""

FUTURES_SPOT_DISLOCATION_OPTION_FINDINGS_V1 = {
    "research_type": "NIFTY_SHORT_SWING_FUTURES_SPOT_DISLOCATION_OPTION_RECORDED_V1",
    "protocol_version": "SHORT_SWING_FUTURES_SPOT_DISLOCATION_OPTIONS_V1",
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "decision": "REJECTED_NO_CANDIDATE_FREEZE",
    "source": {
        "event_dataset_sha256": (
            "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f"
        ),
        "cohort1_options_sha256": (
            "5d640d90e03f4cfb6bfe7c1ba2b2a0c44525b8ab01b4ea4ac62abd350dd9991c"
        ),
        "cohort2_options_sha256": (
            "296d66947da845f3489ad97efec2b0651c0559f7b4ff0d6c486ceba05a8c98cc"
        ),
        "sessions": 152,
        "five_minute_events": 11400,
    },
    "frozen_neighborhood": {
        "percentiles": [80, 90],
        "fixed_exit_minutes": [5, 10],
        "strike_variants": ["ATM", "one_strike_ITM"],
        "primary_slippage_points_per_side": 1.0,
        "severe_slippage_points_per_side": 2.0,
    },
    "result": {
        "ATM_cells_passed_out_of_4": 0,
        "one_strike_ITM_cells_passed_out_of_4": 0,
        "neighborhood_passes": [],
        "all_8_cells_failed": True,
        "best_zero_slippage_cell": {
            "cell_id": "p90_h10_one_strike_ITM",
            "trades": 582,
            "sessions_traded": 139,
            "gross_mean_points": 0.8120274914089347,
            "mean_cost_points_before_slippage": 1.0487153754913032,
            "pooled_net_mean_points": -0.23668788408236835,
            "cohort1_net_mean_points": -0.30881396126337,
            "cohort2_net_mean_points": 0.04099751306448698,
            "positive_chronological_blocks": 7,
            "bootstrap_95pct_points": [
                -1.2656519529089214,
                0.8253709022595939,
            ],
        },
        "best_primary_slippage_cell": {
            "cell_id": "p90_h10_one_strike_ITM",
            "pooled_net_mean_points": -2.2366878840823685,
            "cohort1_net_mean_points": -2.3088139612633696,
            "cohort2_net_mean_points": -1.9590024869355132,
            "positive_chronological_blocks": 1,
            "bootstrap_95pct_points": [
                -3.256703887182138,
                -1.2126244457884308,
            ],
        },
    },
    "interpretation": (
        "The structural futures effect did not monetize through either frozen exact "
        "long-option strike implementation. Even the best cell was negative after "
        "recorded charges at zero slippage, and every cell was materially negative "
        "under the primary one-point-per-side slippage assumption."
    ),
    "guardrail": (
        "Reject for candidate-freeze purposes. Do not rescue with alternate "
        "dislocation thresholds, horizons, strikes, DTE, time-of-day, volume, OI, "
        "stops, targets, spreads, or other post-hoc filters. Do not consume blind data."
    ),
}
