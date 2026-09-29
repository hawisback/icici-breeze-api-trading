"""Recorded rejection of the opening-displacement fade hypothesis.

The prospectively frozen four-cell structural screen failed in every horizon.
This closes the hypothesis without option implementation work, candidate freeze,
blind validation, or post-hoc reversal of the signal direction.
"""

OPENING_DISPLACEMENT_FADE_RECORDED_FINDINGS_V1 = {
    "research_type": "NIFTY_SHORT_SWING_OPENING_DISPLACEMENT_FADE_RECORDED_V1",
    "protocol_version": "SHORT_SWING_OPENING_DISPLACEMENT_FADE_V1",
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
    "derived_abs_opening_displacement_threshold_bps": 12.80071847715558,
    "formulations": {
        "odp25_opt_off_h5": {
            "trades": 112,
            "cohort1_mean_bps": -1.3543231722093747,
            "cohort2_mean_bps": -1.2632705613958333,
            "pooled_mean_bps": -1.3153006247178571,
            "positive_chronological_blocks": 4,
            "bootstrap_95pct_bps": [
                -2.615500646452991,
                0.031242432887253993,
            ],
            "structural_gate_pass": False,
        },
        "odp25_opt_off_h10": {
            "trades": 112,
            "cohort1_mean_bps": -1.2069120794453123,
            "cohort2_mean_bps": -2.57774138006875,
            "pooled_mean_bps": -1.7944103511410712,
            "positive_chronological_blocks": 6,
            "bootstrap_95pct_bps": [
                -3.4820954487458926,
                -0.03459053326850503,
            ],
            "structural_gate_pass": False,
        },
        "odp25_opt_off_h15": {
            "trades": 112,
            "cohort1_mean_bps": -2.488664629446875,
            "cohort2_mean_bps": -4.5851278246125,
            "pooled_mean_bps": -3.387148855946429,
            "positive_chronological_blocks": 4,
            "bootstrap_95pct_bps": [
                -5.694186060871451,
                -1.0529644575545993,
            ],
            "structural_gate_pass": False,
        },
        "odp25_opt_off_h30": {
            "trades": 112,
            "cohort1_mean_bps": -3.0479901551703126,
            "cohort2_mean_bps": -3.539316288747916,
            "pooled_mean_bps": -3.258558498132143,
            "positive_chronological_blocks": 4,
            "bootstrap_95pct_bps": [
                -6.06129642942288,
                -0.5780892666265849,
            ],
            "structural_gate_pass": False,
        },
    },
    "structural_gate_passes": [],
    "decision": "REJECTED_NO_CANDIDATE_FREEZE",
    "interpretation": (
        "All four predeclared fade horizons are negative in both inspected cohorts. "
        "The 10-, 15-, and 30-minute pooled bootstrap intervals are wholly below "
        "zero, while the five-minute interval also has a negative lower bound and "
        "only four of fourteen positive chronological blocks. There is no structural "
        "basis for an exact-option implementation study."
    ),
    "guardrails": {
        "no_option_implementation_protocol": True,
        "no_candidate_freeze": True,
        "no_blind_validation": True,
        "do_not_flip_to_opening_continuation_after_observing_fade_results": True,
        "no_alternate_opening_thresholds": True,
        "no_time_or_DTE_filters": True,
        "no_stop_target_search": True,
        "no_post_hoc_rescue": True,
        "strategy_d_remains_paused": True,
    },
}
