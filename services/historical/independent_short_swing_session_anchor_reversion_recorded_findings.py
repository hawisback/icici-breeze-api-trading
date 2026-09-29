"""Recorded structural pass for session-anchor deviation reversion.

This records the frozen 16-cell development screen exactly as observed. One
predeclared futures formulation passed the structural gate. This is not a
candidate freeze and does not authorize blind validation or live implementation.
"""

SESSION_ANCHOR_REVERSION_RECORDED_FINDINGS_V1 = {
    "research_type": "NIFTY_SHORT_SWING_SESSION_ANCHOR_REVERSION_RECORDED_V1",
    "protocol_version": "SHORT_SWING_SESSION_ANCHOR_REVERSION_V1",
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "source": {
        "event_dataset_sha256": (
            "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f"
        ),
        "sessions": 152,
        "five_minute_events": 11400,
    },
    "derived_abs_anchor_deviation_thresholds_bps": {
        80: 21.570703282817405,
        90: 29.000412956117327,
    },
    "aggregate": {
        "formulations": 16,
        "structural_gate_passes": 1,
        "positive_pooled_mean": 15,
        "positive_mean_both_cohorts": 8,
        "at_least_9_of_14_positive_blocks": 12,
        "bootstrap_95pct_lower_bound_gt_zero": 1,
    },
    "structural_pass": {
        "formulation_id": "ap80_opt_reversion_direction_agreement_h5",
        "abs_anchor_deviation_percentile_min": 80,
        "derived_abs_anchor_deviation_threshold_bps": 21.570703282817405,
        "options_fast_lead_filter": "reversion_direction_agreement",
        "exit_minutes": 5,
        "trades": 1140,
        "trades_by_cohort": {"cohort1": 617, "cohort2": 523},
        "sessions_traded": 115,
        "pooled_mean_bps": 0.39446933933499995,
        "cohort1_mean_bps": 0.5206887771638573,
        "cohort2_mean_bps": 0.2455641899269598,
        "positive_chronological_blocks": 12,
        "session_cluster_bootstrap_mean_95pct_bps": [
            0.002122141423844467,
            0.8137207217664083,
        ],
        "reference_futures_roundtrip_cost_bps_before_slippage": 5.95293863340026,
        "gross_minus_reference_cost_bps": -5.55846929406526,
    },
    "neighborhood_context": {
        "p80_options_agreement_h10": {
            "pooled_mean_bps": 0.4904587992488067,
            "cohort1_mean_bps": 0.5596776490583517,
            "cohort2_mean_bps": 0.41056352016272496,
            "positive_chronological_blocks": 9,
            "bootstrap_lower_bps": -0.13862417708153915,
            "structural_gate_pass": False,
        },
        "p80_options_agreement_h15": {
            "pooled_mean_bps": 0.6069000858335878,
            "cohort1_mean_bps": 0.8178253388664772,
            "cohort2_mean_bps": 0.3618648083828383,
            "positive_chronological_blocks": 10,
            "bootstrap_lower_bps": -0.14630217015371175,
            "structural_gate_pass": False,
        },
        "p80_options_agreement_h30": {
            "pooled_mean_bps": 1.0256660992853848,
            "cohort1_mean_bps": 0.7137540679370195,
            "cohort2_mean_bps": 1.382136992254945,
            "positive_chronological_blocks": 9,
            "bootstrap_lower_bps": -0.3183816755046413,
            "structural_gate_pass": False,
        },
        "p90_options_agreement_h5": {
            "pooled_mean_bps": 0.3759112329101576,
            "cohort1_mean_bps": 0.5109581447339233,
            "cohort2_mean_bps": 0.17857975399525863,
            "positive_chronological_blocks": 12,
            "bootstrap_lower_bps": -0.18445830780024716,
            "structural_gate_pass": False,
        },
    },
    "decision": "STRUCTURAL_PASS_REQUIRES_SEPARATE_OPTIONS_PROTOCOL",
    "interpretation": (
        "The p80 five-minute formulation with options fast-lead agreement is the "
        "only predeclared cell whose pooled session-cluster bootstrap lower bound "
        "is above zero. The pass is narrow: neighboring cells are directionally "
        "supportive but do not pass the frozen bootstrap gate, and the gross futures "
        "effect is far below the reference futures roundtrip cost assumption."
    ),
    "guardrail": (
        "Do not promote the futures structural result directly. Freeze an exact-option "
        "implementation protocol prospectively before inspecting option P&L. Do not "
        "change the p80 threshold, five-minute horizon, fast-lead agreement rule, time, "
        "DTE, volume, OI, stops, targets, or other filters. Keep blind data closed."
    ),
}
