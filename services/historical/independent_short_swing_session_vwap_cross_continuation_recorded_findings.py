"""Recorded rejection of session VWAP cross-through continuation.

The frozen hypothesis was evaluated once on the hash-frozen corrected
232-session V2 development corpus. No fixed horizon passed the predeclared
three-cohort, chronological-block, and session-bootstrap structural gate.

The bar-volume-weighted session VWAP proxy must not be relabeled as exact tick
VWAP. No minimum-history change, distance threshold, volume threshold, filter,
direction flip, or horizon rescue is permitted after this result.
"""

SESSION_VWAP_CROSS_CONTINUATION_RECORDED_FINDINGS_V1 = {
    "research_type": (
        "NIFTY_SHORT_SWING_SESSION_VWAP_CROSS_CONTINUATION_RECORDED_V1"
    ),
    "protocol_version": "SHORT_SWING_SESSION_VWAP_CROSS_CONTINUATION_V1",
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
        "b365858c45c361ed3e07ca35123476f43bcac211cc093ae197a8c1f873119b48"
    ),
    "frozen_rule": {
        "minimum_completed_bars": 6,
        "benchmark": "five-minute bar-volume-weighted session VWAP proxy",
        "cross_up": (
            "previous close below previous proxy and current close above current proxy"
        ),
        "cross_down": (
            "previous close above previous proxy and current close below current proxy"
        ),
        "distance_threshold": None,
        "volume_threshold": None,
        "options_fast_lead_filter": "off",
        "OI_filter": "off",
        "VIX_filter": "off",
        "fixed_exit_minutes": [5, 10, 15, 30],
    },
    "results": {
        "h5": {
            "trades": 1195,
            "pooled_mean_bps": -0.04663861577481166,
            "cohort_mean_bps": {
                "cohort1": -0.14165836837870568,
                "cohort2": -0.040227702314803646,
                "cohort3": 0.06606904433428573,
            },
            "positive_blocks": 11,
            "bootstrap_95pct_bps": [
                -0.411341199067228,
                0.322296500250675,
            ],
            "structural_gate_pass": False,
        },
        "h10": {
            "trades": 922,
            "pooled_mean_bps": 0.3385048684922995,
            "cohort_mean_bps": {
                "cohort1": -0.2966180901167596,
                "cohort2": 0.09596264145486393,
                "cohort3": 1.2821770884618893,
            },
            "positive_blocks": 11,
            "bootstrap_95pct_bps": [
                -0.3205409284061446,
                1.0053156064511284,
            ],
            "structural_gate_pass": False,
        },
        "h15": {
            "trades": 798,
            "pooled_mean_bps": 0.24342723311077702,
            "cohort_mean_bps": {
                "cohort1": 0.2651477507565916,
                "cohort2": -0.4691487929716815,
                "cohort3": 0.8345655507613029,
            },
            "positive_blocks": 14,
            "bootstrap_95pct_bps": [
                -0.5950962809092997,
                1.0900572373441921,
            ],
            "structural_gate_pass": False,
        },
        "h30": {
            "trades": 605,
            "pooled_mean_bps": 0.013315246014214787,
            "cohort_mean_bps": {
                "cohort1": -0.3351902671747864,
                "cohort2": -0.43842399057848824,
                "cohort3": 0.8135636820954775,
            },
            "positive_blocks": 12,
            "bootstrap_95pct_bps": [
                -1.353510774292076,
                1.418661933176007,
            ],
            "structural_gate_pass": False,
        },
    },
    "decision": "REJECTED_NO_CANDIDATE_FREEZE",
    "guardrails": {
        "no_candidate_freeze": True,
        "no_blind_validation": True,
        "bar_vwap_proxy_not_exact_tick_vwap": True,
        "no_alternate_minimum_history": True,
        "no_distance_threshold_search": True,
        "no_volume_threshold_search": True,
        "no_options_fast_lead_rescue": True,
        "no_OI_rescue": True,
        "no_VIX_rescue": True,
        "no_direction_flip": True,
        "no_horizon_rescue": True,
        "no_post_hoc_rescue": True,
        "strategy_d_remains_paused": True,
    },
}
