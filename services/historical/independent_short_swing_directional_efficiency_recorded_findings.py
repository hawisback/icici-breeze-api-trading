"""Recorded directional-efficiency continuation development findings.

This result uses only the already-inspected 152-session development corpus.
The hypothesis and structural gate were committed before outcomes were
inspected. No blind data are used and no candidate is frozen.
"""

DIRECTIONAL_EFFICIENCY_DEVELOPMENT_FINDINGS_V1 = {
    "research_type": "NIFTY_SHORT_SWING_DIRECTIONAL_EFFICIENCY_FINDINGS_V1",
    "protocol_version": "SHORT_SWING_DIRECTIONAL_EFFICIENCY_V1",
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
        "abs_net_return_3_percentile_min": [60, 75],
        "directional_efficiency_min": [0.60, 0.80],
        "options_fast_lead_filter": ["off", "directional_agreement"],
        "fixed_exit_minutes": [5, 10, 15, 30],
        "total_formulations": 32,
    },
    "derived_abs_net_return_3_thresholds_bps": {
        "60": 6.2341071282,
        "75": 9.0178648616,
    },
    "structural_result": {
        "formulations_passing_gate": 0,
        "formulations_positive_mean_in_both_cohorts": 0,
        "formulations_with_positive_bootstrap_lower_bound": 0,
        "formulations_reaching_9_of_14_positive_blocks": 1,
        "cohort1_positive_mean_formulations": "0/32",
        "cohort2_positive_mean_formulations": "31/32",
        "no_options_filter_positive_pooled_formulations": "0/16",
        "options_agreement_positive_pooled_formulations": "5/16",
        "interpretation": (
            "The hypothesis shows a pronounced cohort/regime split rather than "
            "a stable continuation effect. Cohort 1 is negative in every "
            "predeclared formulation while Cohort 2 is positive in all but one. "
            "This is not eligible for rescue with time, DTE, volatility-regime, "
            "threshold, or other post-hoc filters."
        ),
    },
    "best_pooled_predeclared_formulation": {
        "formulation_id": "p60_eff60_opt_directional_agreement_h10",
        "trades": 1379,
        "pooled_mean_bps": 0.22148478746903544,
        "cohort1_mean_bps": -0.12421544088670351,
        "cohort2_mean_bps": 0.601386712694064,
        "positive_chronological_blocks": "9/14",
        "session_cluster_bootstrap_mean_95pct_bps": [
            -0.19390224557495533,
            0.6453123262583212,
        ],
        "reference_current_futures_roundtrip_cost_bps_before_slippage": (
            5.95293863340026
        ),
        "gross_minus_reference_cost_bps": -5.731453845931225,
        "structural_gate_failures": [
            "cohort1_mean_not_positive",
            "bootstrap_lower_bound_not_positive",
        ],
    },
    "non_promotable_low_efficiency_control": {
        "role": (
            "predeclared descriptive control only; it cannot be promoted or used "
            "to rescue the directional-efficiency hypothesis"
        ),
        "abs_net_return_3_percentile_min": 75,
        "directional_efficiency_max": 0.40,
        "exit_minutes": 5,
        "trades": 58,
        "trades_by_cohort": {"cohort1": 44, "cohort2": 14},
        "sessions_traded": 34,
        "pooled_mean_bps": 2.243862017025862,
        "cohort1_mean_bps": 2.224560861959091,
        "cohort2_mean_bps": 2.304522790092857,
        "positive_chronological_blocks": "8/14",
        "session_cluster_bootstrap_mean_95pct_bps": [
            0.6449864604550372,
            3.791986797307,
        ],
        "reference_current_futures_roundtrip_cost_bps_before_slippage": (
            5.95293863340026
        ),
        "gross_minus_reference_cost_bps": -3.709076616374398,
        "why_not_promotable": (
            "It was explicitly declared as a non-promotable control, has only "
            "58 pooled trades and 14 Cohort-2 trades, misses the declared sample "
            "and block-stability gates, and its gross mean is below the reference "
            "current futures roundtrip cost before slippage."
        ),
    },
    "guardrail": (
        "Reject directional-efficiency continuation for candidate-freeze purposes. "
        "Do not consume fresh blind data, do not open an exact-option implementation "
        "study, and do not rescue the cohort split or low-efficiency control with "
        "regime, time, DTE, threshold, stop, target, strike, or other post-hoc filters."
    ),
    "next_research_decision": (
        "Move to a genuinely distinct predeclared behavioral hypothesis that does "
        "not use directional-efficiency thresholds."
    ),
}
