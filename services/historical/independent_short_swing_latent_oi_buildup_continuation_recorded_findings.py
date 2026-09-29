"""Recorded rejection of latent OI build-up continuation.

The frozen four-cell structural screen produced no passing formulation. The
15-minute cell was positive in both inspected cohorts but failed the predeclared
chronological-block and pooled session-cluster bootstrap requirements. This
closes the hypothesis without option implementation, candidate freeze, blind
validation, or threshold/filter rescue.
"""

LATENT_OI_BUILDUP_CONTINUATION_RECORDED_FINDINGS_V1 = {
    "research_type": "NIFTY_SHORT_SWING_LATENT_OI_BUILDUP_CONTINUATION_RECORDED_V1",
    "protocol_version": "SHORT_SWING_LATENT_OI_BUILDUP_CONTINUATION_V1",
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
    "derived_thresholds": {
        "oi_change_bps_min": 12.297854777820001,
        "current_abs_return_bps_max": 2.8922314173999997,
    },
    "formulations": {
        "loi80_ret50_opt_off_h5": {
            "trades": 500,
            "pooled_mean_bps": -0.3768872004835999,
            "cohort1_mean_bps": -0.20274254854816512,
            "cohort2_mean_bps": -0.5115096619088653,
            "positive_chronological_blocks": 5,
            "bootstrap_95pct_bps": [
                -0.7575219489306443,
                -0.0014471179648528104,
            ],
            "structural_gate_pass": False,
        },
        "loi80_ret50_opt_off_h10": {
            "trades": 408,
            "pooled_mean_bps": -0.13261650608112757,
            "cohort1_mean_bps": 0.041736072285026675,
            "cohort2_mean_bps": -0.28014561085248857,
            "positive_chronological_blocks": 8,
            "bootstrap_95pct_bps": [
                -0.7992345969709013,
                0.5164634286235438,
            ],
            "structural_gate_pass": False,
        },
        "loi80_ret50_opt_off_h15": {
            "trades": 370,
            "pooled_mean_bps": 0.22227095482135148,
            "cohort1_mean_bps": 0.3806796274739885,
            "cohort2_mean_bps": 0.08316080066446711,
            "positive_chronological_blocks": 8,
            "bootstrap_95pct_bps": [
                -0.6037664910789918,
                1.0326222411903643,
            ],
            "structural_gate_pass": False,
        },
        "loi80_ret50_opt_off_h30": {
            "trades": 275,
            "pooled_mean_bps": -0.09947430511490904,
            "cohort1_mean_bps": 0.7442948891561538,
            "cohort2_mean_bps": -0.8559570310131033,
            "positive_chronological_blocks": 7,
            "bootstrap_95pct_bps": [
                -1.4622034751641981,
                1.299242972541003,
            ],
            "structural_gate_pass": False,
        },
    },
    "structural_gate_passes": [],
    "decision": "REJECTED_NO_CANDIDATE_FREEZE",
    "interpretation": (
        "The five-minute formulation is negative in both cohorts and its pooled "
        "bootstrap interval is entirely below zero. The ten- and thirty-minute "
        "cells are not positive in Cohort 2. The fifteen-minute cell is positive "
        "in both cohorts but has only eight of fourteen positive chronological "
        "blocks and a negative pooled bootstrap lower bound. It therefore fails "
        "the frozen gate and cannot be promoted."
    ),
    "guardrails": {
        "no_option_implementation_protocol": True,
        "no_candidate_freeze": True,
        "no_blind_validation": True,
        "no_alternate_OI_percentiles": True,
        "no_alternate_return_percentiles": True,
        "no_volume_filter_rescue": True,
        "no_options_filter_rescue": True,
        "no_time_or_DTE_filters": True,
        "no_stop_target_search": True,
        "no_post_hoc_rescue": True,
        "strategy_d_remains_paused": True,
    },
}
