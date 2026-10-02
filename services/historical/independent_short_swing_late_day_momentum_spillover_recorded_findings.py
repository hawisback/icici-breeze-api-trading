"""Recorded rejection of prior-session late-day momentum spillover.

The frozen cross-session hypothesis was evaluated once on the hash-frozen
corrected 232-session V2 development corpus. All four fixed horizons had
negative pooled mean gross returns. Cohorts 1 and 3 were negative at every
horizon, chronological-block stability failed at every horizon, and every
session-cluster bootstrap lower bound was below zero.

The result is closed as run. In particular, reversal, magnitude thresholds,
opening-gap conditioning, first-bar confirmation, and auxiliary filters are
not permitted as post-hoc rescues.
"""

LATE_DAY_MOMENTUM_SPILLOVER_RECORDED_FINDINGS_V1 = {
    "research_type": (
        "NIFTY_SHORT_SWING_LATE_DAY_MOMENTUM_SPILLOVER_RECORDED_V1"
    ),
    "protocol_version": "SHORT_SWING_LATE_DAY_MOMENTUM_SPILLOVER_V1",
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
        "0cd1253aafbefa57f4f086d77ddea6fefc7bb7af459af99d364c5edb1b381600"
    ),
    "signal_sessions": 228,
    "frozen_rule": {
        "signal": "sign of prior session final corrected net_return_6_bps",
        "same_cohort_only": True,
        "entry": "next session 09:20 executable proxy",
        "magnitude_threshold": None,
        "opening_gap_filter": "off",
        "current_first_bar_filter": "off",
        "volume_filter": "off",
        "OI_filter": "off",
        "options_fast_lead_filter": "off",
        "VIX_filter": "off",
        "fixed_exit_minutes": [5, 10, 15, 30],
    },
    "results": {
        "h5": {
            "trades": 228,
            "trades_by_cohort": {
                "cohort1": 79,
                "cohort2": 71,
                "cohort3": 78,
            },
            "pooled_mean_bps": -0.336987580322807,
            "cohort_mean_bps": {
                "cohort1": -0.35258349944430384,
                "cohort2": 0.4128006249408452,
                "cohort3": -1.003691233696154,
            },
            "positive_blocks": 11,
            "bootstrap_95pct_bps": [
                -1.5137587548758002,
                0.8668026019469734,
            ],
            "structural_gate_pass": False,
        },
        "h10": {
            "trades": 228,
            "pooled_mean_bps": -0.5646638067986841,
            "cohort_mean_bps": {
                "cohort1": -1.934632248011392,
                "cohort2": 0.8312220498056336,
                "cohort3": -0.44774571658205103,
            },
            "positive_blocks": 8,
            "bootstrap_95pct_bps": [
                -2.1938425440878726,
                1.064193200108717,
            ],
            "structural_gate_pass": False,
        },
        "h15": {
            "trades": 228,
            "pooled_mean_bps": -0.8583599382013156,
            "cohort_mean_bps": {
                "cohort1": -3.065909209197469,
                "cohort2": 2.243226342569014,
                "cohort3": -1.445747547508974,
            },
            "positive_blocks": 9,
            "bootstrap_95pct_bps": [
                -2.8272969800330476,
                1.1301162078658662,
            ],
            "structural_gate_pass": False,
        },
        "h30": {
            "trades": 228,
            "pooled_mean_bps": -1.847541824067105,
            "cohort_mean_bps": {
                "cohort1": -2.154912243697469,
                "cohort2": 1.7608511593816907,
                "cohort3": -4.820793601939745,
            },
            "positive_blocks": 7,
            "bootstrap_95pct_bps": [
                -4.2007023590723245,
                0.5498673143069627,
            ],
            "structural_gate_pass": False,
        },
    },
    "decision": "REJECTED_NO_CANDIDATE_FREEZE",
    "guardrails": {
        "no_candidate_freeze": True,
        "no_blind_validation": True,
        "no_reversal_rescue": True,
        "no_magnitude_threshold_rescue": True,
        "no_opening_gap_rescue": True,
        "no_current_first_bar_rescue": True,
        "no_volume_OI_options_VIX_rescue": True,
        "no_horizon_rescue": True,
        "no_post_hoc_rescue": True,
        "strategy_d_remains_paused": True,
    },
}
