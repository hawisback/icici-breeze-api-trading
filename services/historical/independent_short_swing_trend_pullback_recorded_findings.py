"""Recorded trend-pullback continuation development findings.

This result uses only the already-inspected 152-session development corpus.
The hypothesis and structural gate were committed before outcomes were
inspected. No blind data are used and no candidate is frozen.
"""

TREND_PULLBACK_DEVELOPMENT_FINDINGS_V1 = {
    "research_type": "NIFTY_SHORT_SWING_TREND_PULLBACK_FINDINGS_V1",
    "protocol_version": "SHORT_SWING_TREND_PULLBACK_V1",
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "decision": "REJECTED_NO_CANDIDATE_FREEZE",
    "source": {
        "event_dataset_sha256": (
            "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f"
        ),
        "sessions": 152,
        "five_minute_events": 11400,
    },
    "predeclared_grid": {
        "impulse_abs_return_percentile_min": [70, 80],
        "pullback_fraction_min": [0.20],
        "pullback_fraction_max": [0.50, 0.75],
        "options_fast_lead_filter": ["off", "original_direction_agreement"],
        "fixed_exit_minutes": [5, 10, 15, 30],
        "total_formulations": 32,
    },
    "derived_impulse_abs_return_thresholds_bps": {
        "70": 7.957143228891849,
        "80": 10.369147189542185,
    },
    "structural_result": {
        "formulations_passing_gate": 0,
        "formulations_positive_mean_in_both_cohorts": 0,
        "formulations_positive_pooled_mean": "4/32",
        "cohort1_positive_mean_formulations": "9/32",
        "cohort2_positive_mean_formulations": "0/32",
        "formulations_reaching_9_of_14_positive_blocks": 0,
        "formulations_with_positive_bootstrap_lower_bound": 0,
        "no_options_filter_positive_pooled_formulations": "0/16",
        "options_agreement_positive_pooled_formulations": "4/16",
        "interpretation": (
            "Trend-pullback continuation fails prospectively. Cohort 2 is negative "
            "in every predeclared formulation. Options agreement modestly improves "
            "some pooled and Cohort-1 means but does not rescue cross-cohort "
            "stability. This is not eligible for post-hoc threshold, time, DTE, "
            "range, volume, OI, stop, target, or execution filtering."
        ),
    },
    "best_pooled_predeclared_formulation": {
        "formulation_id": "p80_pb20to75_opt_original_direction_agreement_h5",
        "trades": 283,
        "pooled_mean_bps": 0.11410551900459358,
        "cohort1_mean_bps": 0.3630826274658683,
        "cohort2_mean_bps": -0.24433566300431037,
        "positive_chronological_blocks": "6/14",
        "session_cluster_bootstrap_mean_95pct_bps": [
            -0.5323291399044666,
            0.7392284823650644,
        ],
        "reference_current_futures_roundtrip_cost_bps_before_slippage": (
            5.95293863340026
        ),
        "gross_minus_reference_cost_bps": -5.838833114395666,
        "structural_gate_failures": [
            "cohort2_mean_not_positive",
            "chronological_block_stability",
            "bootstrap_lower_bound_not_positive",
        ],
    },
    "guardrail": (
        "Reject trend-pullback continuation for candidate-freeze purposes. "
        "Do not consume fresh blind data, do not open an exact-option "
        "implementation study, and do not rescue this result with alternate "
        "pullback fractions, impulse thresholds, lookbacks, time, DTE, range, "
        "volume, OI, stops, targets, strikes, or other post-hoc filters."
    ),
    "next_research_decision": (
        "Move to a genuinely distinct predeclared behavioral hypothesis."
    ),
}
