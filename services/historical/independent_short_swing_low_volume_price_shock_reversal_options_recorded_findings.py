"""Recorded rejection of low-volume price-shock reversal option implementation.

The frozen futures structural effect passed, but the separately frozen exact
directional long-option implementation failed decisively. ATM is the primary
implementation and fails every predeclared implementation criterion. One-strike
ITM also fails and is non-rescuing by protocol. No candidate freeze or blind
validation is justified.
"""

LOW_VOLUME_PRICE_SHOCK_REVERSAL_OPTIONS_RECORDED_FINDINGS_V1 = {
    "research_type": (
        "NIFTY_SHORT_SWING_LOW_VOLUME_PRICE_SHOCK_REVERSAL_OPTIONS_RECORDED_V1"
    ),
    "protocol_version": "SHORT_SWING_LOW_VOLUME_PRICE_SHOCK_REVERSAL_OPTIONS_V1",
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
        "current_abs_return_percentile_min": 70,
        "derived_current_abs_return_threshold_bps": 4.65280279654,
        "volume_vs_prior3_mean_max": 1.0,
        "options_fast_lead_filter": "off",
        "OI_filter": "off",
        "fixed_exit_minutes": 5,
        "structural_signal_count": 1648,
    },
    "ATM_primary": {
        "gross_mean_points": 0.027487864077669823,
        "mean_cost_points_before_slippage": 0.9803301642221715,
        "zero_slippage_net_mean_points": -0.9528423001445016,
        "one_point_per_side": {
            "pooled_net_mean_points": -2.952842300144501,
            "cohort1_net_mean_points": -3.0888562272884736,
            "cohort2_net_mean_points": -2.753802487477912,
            "positive_chronological_blocks": 0,
            "bootstrap_95pct_points": [
                -3.3400223045606774,
                -2.5672739218311253,
            ],
        },
        "two_points_per_side_pooled_net_mean_points": -4.952842300144502,
        "implementation_pass": False,
        "implementation_failures": [
            "primary_slippage_not_positive_both_cohorts",
            "two_point_slippage_pooled_not_positive",
            "primary_slippage_block_stability",
            "primary_slippage_bootstrap_lower_not_positive",
        ],
    },
    "one_strike_ITM_robustness": {
        "gross_mean_points": 0.020631067961165272,
        "mean_cost_points_before_slippage": 1.0462123086727781,
        "zero_slippage_net_mean_points": -1.025581240711613,
        "one_point_per_side": {
            "pooled_net_mean_points": -3.0255812407116127,
            "cohort1_net_mean_points": -3.1682795235628345,
            "cohort2_net_mean_points": -2.8167596877798546,
            "positive_chronological_blocks": 1,
            "bootstrap_95pct_points": [
                -3.4659350656876105,
                -2.5724748073293626,
            ],
        },
        "two_points_per_side_pooled_net_mean_points": -5.025581240711612,
        "implementation_pass": False,
        "can_rescue_ATM": False,
    },
    "decision": "REJECTED_NO_CANDIDATE_FREEZE",
    "interpretation": (
        "The structural futures effect does not translate into a viable exact "
        "directional long-option implementation. ATM gross option movement is only "
        "0.0275 points on average, versus 0.9803 points of modeled charges before "
        "slippage. ATM is negative in both cohorts even at zero slippage and fails "
        "all frozen implementation criteria at one point per side. ITM is likewise "
        "negative and cannot rescue the primary implementation."
    ),
    "guardrails": {
        "no_candidate_freeze": True,
        "no_blind_validation": True,
        "no_options_fast_lead_rescue": True,
        "no_other_return_thresholds": True,
        "no_other_volume_thresholds": True,
        "no_other_horizons": True,
        "no_other_strikes": True,
        "no_time_or_DTE_filters": True,
        "no_stop_target_search": True,
        "no_post_hoc_rescue": True,
        "strategy_d_remains_paused": True,
    },
}
