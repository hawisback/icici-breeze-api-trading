"""Recorded expansion-pause continuation development findings.

This result uses only the already-inspected 152-session development corpus.
The hypothesis and option implementation screen were committed before their
respective results were inspected. No blind data are used.
"""

EXPANSION_PAUSE_DEVELOPMENT_FINDINGS_V1 = {
    "research_type": "NIFTY_SHORT_SWING_EXPANSION_PAUSE_FINDINGS_V1",
    "protocol_version": "SHORT_SWING_EXPANSION_PAUSE_V1",
    "options_protocol_version": "SHORT_SWING_EXPANSION_PAUSE_OPTIONS_V1",
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "decision": "REJECTED_NO_CANDIDATE_FREEZE",
    "source": {
        "event_dataset_sha256": "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f",
        "cohort1_options_sha256": "5d640d90e03f4cfb6bfe7c1ba2b2a0c44525b8ab01b4ea4ac62abd350dd9991c",
        "cohort2_options_sha256": "296d66947da845f3489ad97efec2b0651c0559f7b4ff0d6c486ceba05a8c98cc",
        "sessions": 152,
        "five_minute_events": 11400,
    },
    "hypothesis": (
        "Directional three-bar expansion followed by a narrow inside pause on "
        "contracting volume may continue in the expansion direction."
    ),
    "predeclared_grid": {
        "prior_3_abs_net_return_percentile_min": [70, 80],
        "pause_range_divided_by_prior_3_range_max": [0.33, 0.50],
        "pause_volume_divided_by_prior_3_mean_max": [1.0],
        "options_fast_lead_filter": ["off", "directional_agreement"],
        "fixed_exit_minutes": [5, 10, 15, 30],
        "total_formulations": 32,
    },
    "structural_screen": {
        "formulations_positive_in_both_cohorts": 6,
        "all_cross_cohort_positive_formulations_required_options_agreement": True,
        "best_predeclared_formulation": {
            "prior_3_abs_net_return_percentile_min": 70,
            "derived_threshold_bps": 7.945166206667853,
            "pause_range_divided_by_prior_3_range_max": 0.33,
            "pause_volume_divided_by_prior_3_mean_max": 1.0,
            "inside_pause_required": True,
            "pause_close_in_direction_half_required": True,
            "options_fast_lead_directional_agreement": True,
            "exit_minutes": 10,
            "non_overlapping_trades": True,
            "trades": 181,
            "sessions_traded": 104,
            "trades_per_session": 1.1907894736842106,
            "pooled_mean_bps": 0.7512001745187846,
            "pooled_median_bps": 1.2654773816,
            "win_rate": 0.5469613259668509,
            "cohort1_mean_bps": 0.9911167951645163,
            "cohort2_mean_bps": 0.4976519277,
            "positive_chronological_block_means": "9/14",
            "mean_mfe_bps": 6.747175,
            "mean_mae_bps": -5.749692,
            "session_cluster_bootstrap_mean_95pct_bps": [
                -0.3463848457721041,
                1.8780566265137013,
            ],
            "cohort1_session_cluster_bootstrap_mean_95pct_bps": [
                -0.7016111260076515,
                2.767470082907712,
            ],
            "cohort2_session_cluster_bootstrap_mean_95pct_bps": [
                -0.9169840595669075,
                1.9274779400866568,
            ],
        },
        "without_options_agreement": (
            "None of the 16 predeclared formulations without the options agreement "
            "filter had positive mean return in both cohorts."
        ),
        "interpretation": (
            "The structural futures effect is small and uncertain. It is positive "
            "in both cohorts only when the already-replicated options lead agrees, "
            "and the session-cluster confidence intervals cross zero."
        ),
    },
    "exact_option_implementation": {
        "rule": "best predeclared structural formulation, fixed 10-minute exit",
        "contracts": ["ATM directional long", "one-strike ITM directional long"],
        "lot_size": 65,
        "current_cost_model_reused_from": "SHORT_SWING_DEVELOPMENT_FINDINGS_V1",
        "ATM": {
            "trades": 181,
            "zero_slippage": {
                "pooled_net_mean_points": -0.9627,
                "cohort1_net_mean_points": -0.4655,
                "cohort2_net_mean_points": -1.4882,
            },
            "plus_1_point_each_side": {
                "pooled_net_mean_points": -2.9612,
                "cohort1_net_mean_points": -2.4640,
                "cohort2_net_mean_points": -3.4867,
            },
            "plus_2_points_each_side": {
                "pooled_net_mean_points": -4.9598,
                "cohort1_net_mean_points": -4.4626,
                "cohort2_net_mean_points": -5.4852,
            },
        },
        "one_strike_ITM": {
            "trades": 181,
            "zero_slippage": {
                "pooled_net_mean_points": -0.9475,
                "cohort1_net_mean_points": -0.3640,
                "cohort2_net_mean_points": -1.5642,
            },
            "plus_1_point_each_side": {
                "pooled_net_mean_points": -2.9461,
                "cohort1_net_mean_points": -2.3626,
                "cohort2_net_mean_points": -3.5628,
            },
            "plus_2_points_each_side": {
                "pooled_net_mean_points": -4.9446,
                "cohort1_net_mean_points": -4.3611,
                "cohort2_net_mean_points": -5.5613,
            },
        },
        "interpretation": (
            "Both predeclared exact-contract option implementations are negative "
            "after current charges even before adding slippage. No additional strike, "
            "DTE, time-of-day, stop, target, or spread rescue is permitted."
        ),
    },
    "guardrail": (
        "Reject this hypothesis for candidate-freeze purposes. Do not consume fresh "
        "blind data and do not rescue it with additional filters or execution variants."
    ),
    "next_research_decision": (
        "Move to a genuinely distinct behavioral hypothesis rather than further "
        "tuning expansion-pause continuation."
    ),
}
