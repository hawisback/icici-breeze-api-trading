"""Recorded impulse/OI non-confirmation reversal development findings.

This result uses only the already-inspected 152-session development corpus.
The structural hypothesis and exact-option implementation screen were committed
before their respective results were inspected. No blind data are used.
"""

IMPULSE_OI_REVERSAL_DEVELOPMENT_FINDINGS_V1 = {
    "research_type": "NIFTY_SHORT_SWING_IMPULSE_OI_REVERSAL_FINDINGS_V1",
    "protocol_version": "SHORT_SWING_IMPULSE_OI_REVERSAL_V1",
    "options_protocol_version": "SHORT_SWING_IMPULSE_OI_REVERSAL_OPTIONS_V1",
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
        "A large one-bar price displacement on elevated volume with falling open "
        "interest may represent covering/liquidation rather than fresh position "
        "building and may reverse over the next 5-30 minutes."
    ),
    "predeclared_grid": {
        "current_abs_return_percentile_min": [80, 90],
        "volume_vs_prior3_mean_min": [1.2, 1.5],
        "options_fast_lead_filter": ["off", "agrees_with_reversal"],
        "fixed_exit_minutes": [5, 10, 15, 30],
        "total_formulations": 32,
    },
    "derived_current_abs_return_threshold_bps": {
        "p80": 6.04544680668,
        "p90": 8.465457602550009,
    },
    "structural_screen": {
        "formulations_positive_in_both_cohorts": 1,
        "only_cross_cohort_positive_formulation": {
            "current_abs_return_percentile_min": 90,
            "current_abs_return_threshold_bps": 8.465457602550009,
            "volume_vs_prior3_mean_min": 1.2,
            "oi_change_bps_max": 0.0,
            "options_fast_lead_filter": "off",
            "exit_minutes": 30,
            "non_overlapping_trades": True,
            "trades": 205,
            "sessions_traded": 111,
            "trades_per_session": 1.3486842105263157,
            "pooled_mean_bps": 1.9362997891717069,
            "pooled_median_bps": 2.363254,
            "win_rate": 0.5609756097560976,
            "cohort1_mean_bps": 3.3658978966310347,
            "cohort2_mean_bps": 0.07300337944943831,
            "positive_chronological_block_means": "10/14",
            "mean_mfe_bps": 12.748617,
            "mean_mae_bps": -10.636201,
            "session_cluster_bootstrap_mean_95pct_bps": [
                -0.08536083,
                3.88842144,
            ],
            "cohort1_session_cluster_bootstrap_mean_95pct_bps": [
                0.69560435,
                5.84959677,
            ],
            "cohort2_session_cluster_bootstrap_mean_95pct_bps": [
                -2.99862625,
                2.91339394,
            ],
        },
        "interpretation": (
            "The only cross-cohort-positive formulation is carried almost entirely "
            "by Cohort 1; Cohort 2 is effectively flat and its cluster-bootstrap "
            "interval is wide around zero."
        ),
    },
    "oi_confirmation_control": {
        "role": "descriptive control only; not promotable under this hypothesis",
        "best_control_formulation": {
            "current_abs_return_percentile_min": 80,
            "volume_vs_prior3_mean_min": 1.5,
            "oi_change_bps_min_exclusive": 0.0,
            "options_fast_lead_filter": "off",
            "exit_minutes": 30,
            "trades": 276,
            "pooled_mean_bps": 1.531092,
            "cohort1_mean_bps": 1.369452,
            "cohort2_mean_bps": 1.715286,
            "positive_chronological_block_means": "10/14",
        },
        "interpretation": (
            "A comparable or stronger reversal effect also appears when OI rises. "
            "Therefore falling OI is not a distinctive explanatory condition for "
            "the observed reversal behavior."
        ),
    },
    "futures_cost_screen": {
        "gross_edge_bps": 1.9362997891717069,
        "reference_current_roundtrip_cost_bps_before_slippage": 5.95293863340026,
        "conclusion": "CURRENT_FUTURES_COSTS_EXCEED_GROSS_EDGE",
    },
    "exact_option_implementation": {
        "rule": "only cross-cohort-positive structural formulation; fixed 30-minute exit",
        "contracts": ["ATM directional reversal long", "one-strike ITM directional reversal long"],
        "lot_size": 65,
        "current_cost_model_reused_from": "SHORT_SWING_DEVELOPMENT_FINDINGS_V1",
        "ATM": {
            "trades": 205,
            "zero_slippage": {
                "gross_mean_points": 2.5488,
                "pooled_net_mean_points": 1.5679,
                "cohort1_net_mean_points": 2.8376,
                "cohort2_net_mean_points": -0.0869,
            },
            "plus_1_point_each_side": {
                "pooled_net_mean_points": -0.4306,
                "cohort1_net_mean_points": 0.8391,
                "cohort2_net_mean_points": -2.0854,
            },
            "plus_2_points_each_side": {
                "pooled_net_mean_points": -2.4291,
                "cohort1_net_mean_points": -1.1595,
                "cohort2_net_mean_points": -4.0839,
            },
        },
        "one_strike_ITM": {
            "trades": 205,
            "zero_slippage": {
                "gross_mean_points": 2.8456,
                "pooled_net_mean_points": 1.7969,
                "cohort1_net_mean_points": 3.2645,
                "cohort2_net_mean_points": -0.1160,
            },
            "plus_1_point_each_side": {
                "pooled_net_mean_points": -0.2017,
                "cohort1_net_mean_points": 1.2660,
                "cohort2_net_mean_points": -2.1145,
            },
            "plus_2_points_each_side": {
                "pooled_net_mean_points": -2.2002,
                "cohort1_net_mean_points": -0.7326,
                "cohort2_net_mean_points": -4.1130,
            },
        },
        "interpretation": (
            "Neither predeclared exact-contract option implementation is positive "
            "in Cohort 2 even before slippage. With one premium point of adverse "
            "slippage per side, both are negative pooled."
        ),
    },
    "guardrail": (
        "Reject this hypothesis for candidate-freeze purposes. Do not consume fresh "
        "blind data and do not rescue it with OI thresholds, DTE, time-of-day, "
        "additional strikes, stops, targets, spreads, or other post-hoc filters."
    ),
    "next_research_decision": (
        "Move to a genuinely distinct behavioral hypothesis rather than further "
        "tuning impulse/OI reversal."
    ),
}
