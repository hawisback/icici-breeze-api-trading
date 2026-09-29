"""Recorded rejection of compression-release continuation.

The frozen four-cell structural screen failed the predeclared cross-cohort,
chronological-block, and bootstrap requirements. This closes the hypothesis
without option implementation research, candidate freeze, blind validation,
or post-hoc ratio/filter rescue.
"""

COMPRESSION_RELEASE_CONTINUATION_RECORDED_FINDINGS_V1 = {
    "research_type": "NIFTY_SHORT_SWING_COMPRESSION_RELEASE_CONTINUATION_RECORDED_V1",
    "protocol_version": "SHORT_SWING_COMPRESSION_RELEASE_CONTINUATION_V1",
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
    "frozen_rule": {
        "compression_ratio_max": 0.75,
        "release_expansion_ratio_min": 1.50,
        "long_close_location_min": 0.75,
        "short_close_location_max": 0.25,
        "options_fast_lead_filter": "off",
        "fixed_exit_minutes": [5, 10, 15, 30],
    },
    "formulations": {
        "crc_opt_off_h5": {
            "trades": 357,
            "pooled_mean_bps": -0.32872245474929973,
            "cohort1_mean_bps": -0.9832557909414013,
            "cohort2_mean_bps": 0.1850862141615,
            "positive_chronological_blocks": 6,
            "bootstrap_95pct_bps": [
                -0.9395716879196051,
                0.28598221319622535,
            ],
            "structural_gate_pass": False,
        },
        "crc_opt_off_h10": {
            "trades": 341,
            "pooled_mean_bps": 0.18284761041788852,
            "cohort1_mean_bps": -0.46166992371496607,
            "cohort2_mean_bps": 0.6712191440134021,
            "positive_chronological_blocks": 7,
            "bootstrap_95pct_bps": [
                -0.7028603363627348,
                1.0573008699326607,
            ],
            "structural_gate_pass": False,
        },
        "crc_opt_off_h15": {
            "trades": 341,
            "pooled_mean_bps": 0.15656407964046928,
            "cohort1_mean_bps": -0.36738806623197284,
            "cohort2_mean_bps": 0.5535793654304123,
            "positive_chronological_blocks": 7,
            "bootstrap_95pct_bps": [
                -0.8268315314707816,
                1.1779080829182427,
            ],
            "structural_gate_pass": False,
        },
        "crc_opt_off_h30": {
            "trades": 308,
            "pooled_mean_bps": -1.4027534605662337,
            "cohort1_mean_bps": -2.315562742559124,
            "cohort2_mean_bps": -0.6714384217766081,
            "positive_chronological_blocks": 5,
            "bootstrap_95pct_bps": [
                -2.8032602091046313,
                0.03572207428476909,
            ],
            "structural_gate_pass": False,
        },
    },
    "structural_gate_passes": [],
    "decision": "REJECTED_NO_CANDIDATE_FREEZE",
    "interpretation": (
        "Cohort 1 is negative at every frozen horizon. The small positive pooled "
        "means at ten and fifteen minutes are driven by Cohort 2 and fail both "
        "chronological-block stability and the pooled session-cluster bootstrap "
        "lower-bound requirement. Thirty minutes is negative in both cohorts."
    ),
    "guardrails": {
        "no_option_implementation_protocol": True,
        "no_candidate_freeze": True,
        "no_blind_validation": True,
        "no_alternate_compression_ratios": True,
        "no_alternate_expansion_ratios": True,
        "no_options_filter_rescue": True,
        "no_time_or_DTE_filters": True,
        "no_stop_target_search": True,
        "no_post_hoc_rescue": True,
        "strategy_d_remains_paused": True,
    },
}
