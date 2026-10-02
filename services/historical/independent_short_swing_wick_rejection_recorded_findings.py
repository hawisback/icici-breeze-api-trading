"""Recorded single-bar wick-rejection reversal development findings.

This result uses only the already-inspected 152-session development corpus.
The hypothesis and structural gate were committed before outcomes were
inspected. No blind data are used and no candidate is frozen.
"""

WICK_REJECTION_DEVELOPMENT_FINDINGS_V1 = {
    "research_type": "NIFTY_SHORT_SWING_WICK_REJECTION_FINDINGS_V1",
    "protocol_version": "SHORT_SWING_WICK_REJECTION_V1",
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
        "current_bar_range_percentile_min": [50],
        "dominant_wick_fraction_min": [0.45, 0.60],
        "body_fraction_max": [0.25, 0.40],
        "options_fast_lead_filter": ["off", "reversal_direction_agreement"],
        "fixed_exit_minutes": [5, 10, 15, 30],
        "total_formulations": 32,
    },
    "derived_current_bar_range_thresholds_bps": {
        "50": 7.1587803101091545,
    },
    "structural_result": {
        "formulations_passing_gate": 0,
        "formulations_positive_mean_in_both_cohorts": 5,
        "formulations_positive_pooled_mean": "9/32",
        "cohort1_positive_mean_formulations": "7/32",
        "cohort2_positive_mean_formulations": "17/32",
        "formulations_reaching_9_of_14_positive_blocks": 2,
        "formulations_with_positive_bootstrap_lower_bound": 0,
        "no_options_filter_positive_pooled_formulations": "2/16",
        "options_agreement_positive_pooled_formulations": "7/16",
        "interpretation": (
            "The wick-rejection idea shows several weak positive pockets, "
            "especially when the options fast lead agrees with the reversal "
            "direction, but no predeclared formulation has a positive pooled "
            "session-cluster bootstrap lower bound. The largest gross mean also "
            "remains far below the reference futures roundtrip cost before "
            "slippage. The study therefore fails prospectively and is not "
            "eligible for threshold, time, DTE, wick-definition, or execution rescue."
        ),
    },
    "closest_to_structural_gate": {
        "formulation_id": (
            "rp50_wick45_body40_opt_reversal_direction_agreement_h5"
        ),
        "trades": 727,
        "pooled_mean_bps": 0.05656115941746909,
        "cohort1_mean_bps": 0.046719809690411014,
        "cohort2_mean_bps": 0.07147642301764702,
        "positive_chronological_blocks": "9/14",
        "session_cluster_bootstrap_mean_95pct_bps": [
            -0.3758926328481505,
            0.4950910511745267,
        ],
        "reference_current_futures_roundtrip_cost_bps_before_slippage": (
            5.95293863340026
        ),
        "gross_minus_reference_cost_bps": -5.896377473982791,
        "structural_gate_failures": [
            "bootstrap_lower_bound_not_positive",
        ],
    },
    "largest_pooled_gross_mean": {
        "formulation_id": "rp50_wick60_body40_opt_off_h30",
        "trades": 529,
        "pooled_mean_bps": 0.6598434162561438,
        "cohort1_mean_bps": 0.7385442413263157,
        "cohort2_mean_bps": 0.536443578888835,
        "positive_chronological_blocks": "8/14",
        "session_cluster_bootstrap_mean_95pct_bps": [
            -0.44078107698562136,
            1.8438353095594442,
        ],
        "reference_current_futures_roundtrip_cost_bps_before_slippage": (
            5.95293863340026
        ),
        "gross_minus_reference_cost_bps": -5.293095217144116,
        "structural_gate_failures": [
            "chronological_block_stability",
            "bootstrap_lower_bound_not_positive",
        ],
    },
    "guardrail": (
        "Reject single-bar wick-rejection reversal for candidate-freeze purposes. "
        "Do not consume fresh blind data, do not open an exact-option implementation "
        "study, and do not rescue the result with alternate wick definitions, "
        "range thresholds, body thresholds, time, DTE, volume, OI, stops, targets, "
        "strikes, or other post-hoc filters."
    ),
    "next_research_decision": (
        "Move to a genuinely distinct predeclared cross-market behavioral hypothesis."
    ),
}
