"""Recorded rejection of session-anchor exact-option implementation.

The frozen futures structural screen had one passing cell, but the prospectively
frozen exact-option implementation failed. This closes the hypothesis without
candidate freeze, blind validation, or post-hoc rescue.
"""

SESSION_ANCHOR_REVERSION_OPTION_RECORDED_FINDINGS_V1 = {
    "research_type": "NIFTY_SHORT_SWING_SESSION_ANCHOR_REVERSION_OPTION_RECORDED_V1",
    "protocol_version": "SHORT_SWING_SESSION_ANCHOR_REVERSION_OPTIONS_V1",
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
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
    "structural_rule": {
        "abs_anchor_deviation_percentile_min": 80,
        "derived_abs_anchor_deviation_threshold_bps": 21.570703282817405,
        "options_fast_lead_filter": "reversion_direction_agreement",
        "fixed_exit_minutes": 5,
        "structural_signal_count": 1140,
    },
    "primary_ATM": {
        "gross_mean_points": -0.099342105263158,
        "mean_cost_points_before_slippage": 0.9902084710935832,
        "zero_slippage_net_mean_points": -1.089550576356741,
        "primary_1pt_slippage_net_mean_points": -3.0895505763567406,
        "cohort1_1pt_net_mean_points": -3.028638371224511,
        "cohort2_1pt_net_mean_points": -3.1614106730423734,
        "positive_chronological_blocks_at_1pt": 1,
        "bootstrap_95pct_at_1pt": [
            -3.5739753098097906,
            -2.5855709807326153,
        ],
        "two_point_slippage_net_mean_points": -5.089550576356741,
        "implementation_pass": False,
    },
    "robustness_one_strike_ITM": {
        "gross_mean_points": -0.17210526315789482,
        "mean_cost_points_before_slippage": 1.0568680441366531,
        "zero_slippage_net_mean_points": -1.228973307294548,
        "primary_1pt_slippage_net_mean_points": -3.2289733072945483,
        "cohort1_1pt_net_mean_points": -3.235236577615921,
        "cohort2_1pt_net_mean_points": -3.221584324907766,
        "positive_chronological_blocks_at_1pt": 1,
        "bootstrap_95pct_at_1pt": [
            -3.788610596179584,
            -2.6639499082140943,
        ],
        "two_point_slippage_net_mean_points": -5.228973307294548,
        "implementation_pass": False,
        "can_rescue_primary": False,
    },
    "decision": "REJECTED_NO_CANDIDATE_FREEZE",
    "interpretation": (
        "The directional long-option implementation is negative even before "
        "slippage for both ATM and one-strike-ITM. At the frozen primary one-point "
        "per-side slippage assumption, both cohorts are negative and the pooled "
        "session-cluster bootstrap interval is wholly below zero. The structural "
        "futures pass therefore does not translate into an implementable option edge."
    ),
    "guardrails": {
        "no_candidate_freeze": True,
        "no_blind_validation": True,
        "no_ITM_rescue": True,
        "no_alternate_thresholds": True,
        "no_alternate_horizons": True,
        "no_alternate_strikes": True,
        "no_time_or_DTE_filters": True,
        "no_stop_target_search": True,
        "no_post_hoc_rescue": True,
        "strategy_d_remains_paused": True,
    },
}
