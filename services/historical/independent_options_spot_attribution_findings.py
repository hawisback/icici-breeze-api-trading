"""Recorded development-only spot attribution for the options lead/lag observation.

This is not an O3 freeze. No blind data was inspected and implementation remains
disallowed.
"""

DEVELOPMENT_OPTIONS_SPOT_ATTRIBUTION_V1 = {
    "research_type": "NIFTY_OPTIONS_SPOT_ATTRIBUTION_V1",
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "source": {
        "options_sha256": "5d640d90e03f4cfb6bfe7c1ba2b2a0c44525b8ab01b4ea4ac62abd350dd9991c",
        "spot_sha256": "372703608cc10643e8f6fdb0ea4c0c91ba8676c19c4d3fe6c38b596304aab0cb",
        "sessions": 80,
        "aligned_rows": 6000,
        "scorable_next_5m_rows": 5840,
        "spot_qa": {
            "complete_75_bar_sessions": "80/80",
            "duplicate_rows": 0,
            "invalid_ohlc_rows": 0,
            "failed_requests": 0,
            "grid_issue_sessions": 0,
            "source": "BREEZE",
            "instrument": "NIFTY 50",
        },
    },
    "pooled_attribution": {
        "options_gap_vs_spot_gap_spearman": 0.7233397490115316,
        "options_gap_vs_next_5m_futures_spearman": 0.1695929690736368,
        "spot_gap_vs_next_5m_futures_spearman": 0.15160767029062508,
        "combined_model": {
            "current_futures_return_coefficient": -0.005615017839699161,
            "spot_gap_coefficient": 0.0844385120290618,
            "options_gap_coefficient": 0.3247766303712448,
            "r_squared": 0.024419182617730684,
        },
        "incremental_r2_options_over_spot": 0.00877114727953554,
        "incremental_r2_spot_over_options": 0.0009808861329102525,
        "options_gap_coefficient_positive_blocks": "8/8",
        "incremental_r2_options_positive_blocks": "8/8",
        "session_cluster_bootstrap_options_coefficient_95pct": [
            0.22409056692519402,
            0.4316934342672246,
        ],
        "bootstrap_seed": 12345,
        "bootstrap_resamples": 10000,
        "bootstrap_is_post_hoc_development_diagnostic": True,
    },
    "crossfit_incremental_options_component": {
        "method": (
            "leave one chronological 10-session block out; fit options gap from "
            "spot gap plus current futures return on the other seven blocks, then "
            "score the held-out residual against the next futures bar"
        ),
        "spearman_vs_next_5m_futures": 0.12814607438239703,
        "positive_spearman_blocks": "8/8",
        "mean_direction_aligned_next_5m_bps": 0.5749963272047998,
        "median_direction_aligned_next_5m_bps": 0.5355163059628243,
        "direction_hit_rate": 0.5450342465753425,
        "positive_mean_blocks": "8/8",
        "point_move_recalculation_spearman": 0.12831565825297586,
    },
    "timestamp_alignment_challenge": {
        "synthetic_return_vs_same_bar_spot_return_spearman": 0.9279385017309227,
        "synthetic_return_vs_previous_bar_spot_return_spearman": -0.03235977016464757,
        "synthetic_return_vs_next_bar_spot_return_spearman": -0.006747415783021351,
        "interpretation": (
            "The options synthetic return is aligned with the same labeled spot bar, "
            "not shifted by one 5-minute bar."
        ),
    },
    "atm_switch_challenge": {
        "stable_atm_rows": 4744,
        "stable_atm_options_gap_spearman": 0.17015677435441162,
        "atm_switch_rows": 1096,
        "atm_switch_options_gap_spearman": 0.166558910404827,
        "interpretation": "The relationship is not concentrated in ATM strike-transition bars.",
    },
    "multi_strike_staleness_challenge": {
        "policy": (
            "descriptive robustness only; evaluate symmetric ATM-only, ATM +/-1, "
            "ATM +/-2 and ATM +/-4 median synthetic forwards without selecting a best width"
        ),
        "raw_spearman_by_half_width": {
            "0": 0.1695929690736368,
            "1": 0.17026759399291722,
            "2": 0.16953667790204632,
            "4": 0.1684751396675509,
        },
        "crossfit_incremental_spearman_by_half_width": {
            "0": 0.12814607438239703,
            "1": 0.12872264772770295,
            "2": 0.12803384352692054,
            "4": 0.12631555370968864,
        },
        "positive_raw_blocks_each_width": "8/8",
        "positive_crossfit_blocks_each_width": "8/8",
        "interpretation": (
            "The development relationship is not dependent on one ATM CE/PE close; "
            "symmetric multi-strike aggregation leaves it essentially unchanged."
        ),
    },
    "execution_timing_challenge": {
        "reason": (
            "A completed 5-minute bar cannot be acted on at its already-observed close; "
            "the earliest simple bar-level execution proxy is the next bar open."
        ),
        "crossfit_component_vs_current_close_to_next_open_spearman": 0.15867357045070798,
        "crossfit_component_vs_next_open_to_next_close_spearman": 0.09785121668836147,
        "next_open_to_close_positive_spearman_blocks": "8/8",
        "next_open_to_close_mean_direction_aligned_bps": 0.43536103585608393,
        "next_open_to_close_direction_hit_rate": 0.5330479452054795,
        "session_cluster_bootstrap_mean_aligned_open_to_close_95pct_bps": [
            0.28999757,
            0.57956668,
        ],
        "bootstrap_seed": 2468,
        "bootstrap_resamples": 10000,
        "interpretation": (
            "Some predictive information survives an executable next-bar-open timing "
            "proxy, but the gross magnitude is small and must not be assumed tradable."
        ),
    },
    "magnitude_quartiles_development_only": {
        "warning": "descriptive only; no magnitude threshold is frozen",
        "next_open_to_close_mean_aligned_bps_q1_to_q4": [
            0.23991699754483953,
            0.18208170306132668,
            0.617497651241555,
            0.7019477915766145,
        ],
        "positive_mean_blocks_q1_to_q4": ["5/8", "4/8", "8/8", "8/8"],
        "interpretation": (
            "Larger residual magnitudes are stronger in the upper half, but the "
            "quartiles are not strictly monotonic and must not be mined into a rescue filter."
        ),
    },
    "status": "PROMISING_INCREMENTAL_MICROSTRUCTURE_LEAD_BUT_NOT_YET_O3",
    "next_research_decision": (
        "Do not spend blind data yet. Preserve the continuous feature, avoid threshold "
        "selection, and determine whether the modest next-open-to-close effect has a "
        "credible execution/economic interpretation before any O3 freeze."
    ),
}
