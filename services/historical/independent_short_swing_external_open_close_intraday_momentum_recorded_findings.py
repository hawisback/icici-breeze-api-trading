"""Recorded rejection of externally motivated opening-to-closing intraday momentum.

The exact externally specified rule was frozen before additional V2 outcome
inspection. It produced positive pooled and positive mean gross returns in all
three development cohorts, but failed the predeclared chronological-block and
session-cluster-bootstrap stability gates.

No magnitude, volume, volatility, overnight, OI, options, VIX, timing, or
directional rescue is permitted. The result does not authorize candidate
freeze, blind validation, or implementation.
"""

EXTERNAL_OPEN_CLOSE_INTRADAY_MOMENTUM_RECORDED_FINDINGS_V1 = {
    "research_type": (
        "NIFTY_SHORT_SWING_EXTERNAL_OPEN_CLOSE_INTRADAY_MOMENTUM_RECORDED_V1"
    ),
    "protocol_version": "SHORT_SWING_EXTERNAL_OPEN_CLOSE_INTRADAY_MOMENTUM_V1",
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
        "2d1f5633e4adb1d068ed26cc69d1a0c942b0d726f669264cc4a407051a1ca722"
    ),
    "frozen_rule": {
        "signal": "sign of opening-half-hour corrected net_return_6_bps",
        "signal_row_time": "09:40",
        "trade_row_time": "14:55",
        "entry_time": "15:00 one-minute futures open",
        "exit_time": "15:29 one-minute futures close",
        "holding_minutes": 30,
        "signal_magnitude_threshold": None,
        "volume_filter": "off",
        "volatility_filter": "off",
        "OI_filter": "off",
        "options_fast_lead_filter": "off",
        "VIX_filter": "off",
        "overnight_component": "excluded",
    },
    "result": {
        "trades": 232,
        "trades_by_cohort": {
            "cohort1": 80,
            "cohort2": 72,
            "cohort3": 80,
        },
        "pooled_mean_bps": 1.8489231764827585,
        "pooled_median_bps": 1.2162891423,
        "cohort_mean_bps": {
            "cohort1": 1.0303803681412496,
            "cohort2": 1.5725148597430558,
            "cohort3": 2.9162334698900008,
        },
        "win_rate": 0.5689655172413793,
        "positive_blocks": 13,
        "required_positive_blocks": 15,
        "bootstrap_95pct_bps": [
            -0.29609719668615325,
            3.967238711386713,
        ],
        "structural_gate_pass": False,
        "failures": [
            "chronological_block_stability",
            "bootstrap_lower_bound_not_positive",
        ],
    },
    "decision": "REJECTED_NO_CANDIDATE_FREEZE",
    "next_research_decision": (
        "Do not open another ad-hoc or literature-selected V2 hypothesis. Freeze "
        "a new inspected development cohort before further endogenous strategy "
        "development. Blind data remains untouched."
    ),
    "guardrails": {
        "no_candidate_freeze": True,
        "no_blind_validation": True,
        "no_signal_magnitude_rescue": True,
        "no_volume_or_volatility_rescue": True,
        "no_overnight_rescue": True,
        "no_OI_options_VIX_rescue": True,
        "no_alternate_entry_or_exit_time": True,
        "no_direction_flip": True,
        "no_post_hoc_rescue": True,
        "no_additional_v2_hypothesis_before_new_development_evidence": True,
        "strategy_d_remains_paused": True,
    },
}
