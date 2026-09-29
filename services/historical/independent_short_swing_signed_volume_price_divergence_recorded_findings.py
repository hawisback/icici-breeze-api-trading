"""Recorded rejection of signed-volume / price-divergence catch-up.

The frozen hypothesis was evaluated exactly once on the hash-frozen corrected
232-session V2 development corpus. No formulation passed the predeclared
three-cohort / chronological-block / session-bootstrap structural gate.

The positive pooled means at longer horizons do not justify rescue: the
15-minute cell has only 11/22 positive blocks and a negative bootstrap lower
bound; the 30-minute cell has 14/22 positive blocks (below the frozen 15/22
minimum) and a negative bootstrap lower bound.

No alternate imbalance threshold, direction flip, options/OI filter, or
post-hoc horizon rescue is permitted.
"""

SIGNED_VOLUME_PRICE_DIVERGENCE_RECORDED_FINDINGS_V1 = {
    "research_type": (
        "NIFTY_SHORT_SWING_SIGNED_VOLUME_PRICE_DIVERGENCE_RECORDED_V1"
    ),
    "protocol_version": "SHORT_SWING_SIGNED_VOLUME_PRICE_DIVERGENCE_V1",
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "source": {
        "event_dataset_sha256": (
            "e4c08b2bbf8488c9a5bd1fd6229b242f6ce9f0e27060c2cce16b03886724ab46"
        ),
        "sessions": 232,
        "five_minute_events": 17400,
        "chronological_blocks": 22,
    },
    "findings_artifact_sha256": (
        "652639cb6122ea6935eaab95376c38c462d7dc8b95fe26a874abf1444ec8ea47"
    ),
    "frozen_rule": {
        "window_bars": 3,
        "abs_signed_volume_imbalance_3_min": 1.0 / 3.0,
        "divergence_required": True,
        "signal_direction": "sign(signed_volume_imbalance_3)",
        "options_fast_lead_filter": "off",
        "OI_filter": "off",
        "fixed_exit_minutes": [5, 10, 15, 30],
    },
    "results": {
        "h5": {
            "trades": 1220,
            "trades_by_cohort": {
                "cohort1": 465,
                "cohort2": 358,
                "cohort3": 397,
            },
            "pooled_mean_bps": 0.18390490379795085,
            "cohort_mean_bps": {
                "cohort1": 0.21035525286451617,
                "cohort2": 0.3610956998798883,
                "cohort3": -0.00686012721788405,
            },
            "positive_blocks": 13,
            "bootstrap_95pct_bps": [
                -0.17637958196695325,
                0.5420448328427463,
            ],
            "structural_gate_pass": False,
            "failures": [
                "cohort3_mean_not_positive",
                "chronological_block_stability",
                "bootstrap_lower_bound_not_positive",
            ],
        },
        "h10": {
            "trades": 1007,
            "trades_by_cohort": {
                "cohort1": 385,
                "cohort2": 295,
                "cohort3": 327,
            },
            "pooled_mean_bps": 0.050173997912313785,
            "cohort_mean_bps": {
                "cohort1": -0.09321136058415586,
                "cohort2": 0.221432778320678,
                "cohort3": 0.06449211045259931,
            },
            "positive_blocks": 11,
            "bootstrap_95pct_bps": [
                -0.4902779460019368,
                0.5966846951793648,
            ],
            "structural_gate_pass": False,
            "failures": [
                "cohort1_mean_not_positive",
                "chronological_block_stability",
                "bootstrap_lower_bound_not_positive",
            ],
        },
        "h15": {
            "trades": 921,
            "trades_by_cohort": {
                "cohort1": 353,
                "cohort2": 266,
                "cohort3": 302,
            },
            "pooled_mean_bps": 0.3926554398029317,
            "cohort_mean_bps": {
                "cohort1": 0.14557491733484423,
                "cohort2": 0.5329485103947369,
                "cohort3": 0.557892087663245,
            },
            "positive_blocks": 11,
            "bootstrap_95pct_bps": [
                -0.36473430943025925,
                1.1455074135790078,
            ],
            "structural_gate_pass": False,
            "failures": [
                "chronological_block_stability",
                "bootstrap_lower_bound_not_positive",
            ],
        },
        "h30": {
            "trades": 763,
            "trades_by_cohort": {
                "cohort1": 288,
                "cohort2": 225,
                "cohort3": 250,
            },
            "pooled_mean_bps": 0.6044078043870248,
            "cohort_mean_bps": {
                "cohort1": 0.8253224041437499,
                "cohort2": 0.2656695410688888,
                "cohort3": 0.6547786224535996,
            },
            "positive_blocks": 14,
            "bootstrap_95pct_bps": [
                -0.5988865426147468,
                1.7998892993344544,
            ],
            "structural_gate_pass": False,
            "failures": [
                "chronological_block_stability",
                "bootstrap_lower_bound_not_positive",
            ],
        },
    },
    "decision": "REJECTED_NO_CANDIDATE_FREEZE",
    "guardrails": {
        "no_candidate_freeze": True,
        "no_blind_validation": True,
        "no_alternate_imbalance_thresholds": True,
        "no_direction_flip": True,
        "no_options_fast_lead_rescue": True,
        "no_OI_rescue": True,
        "no_horizon_rescue": True,
        "no_post_hoc_rescue": True,
        "prior_study_decisions_remain_closed": True,
        "strategy_d_remains_paused": True,
    },
}
