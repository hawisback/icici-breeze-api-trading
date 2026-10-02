"""Frozen protocol for prior-session late-day momentum spillover.

Hypothesis
----------
Directional price pressure in the final 30 minutes of one NIFTY futures session
may persist into the next session's early trading.

Signal construction is cross-session and deliberately simple:
- within each development cohort, take the final five-minute event of session D,
- use the sign of its corrected V2 net_return_6_bps (the final six five-minute
  bars, i.e. approximately the last 30 minutes),
- apply that direction to the next session D+1,
- enter only at the existing conservative 09:20 executable proxy represented by
  the first event row's t+5 one-minute open.

The signal is therefore known before the next session opens; the 09:20 entry is
used only because the frozen V2 event artifact already provides exact one-minute
fixed-horizon outcomes from that execution point for all cohorts.

No magnitude threshold, opening-gap condition, volume/OI/options/VIX filter,
or reversal alternative is tested.
"""
from __future__ import annotations

PROTOCOL_VERSION = "SHORT_SWING_LATE_DAY_MOMENTUM_SPILLOVER_V1"
SOURCE_EVENT_SHA256 = (
    "e4c08b2bbf8488c9a5bd1fd6229b242f6ce9f0e27060c2cce16b03886724ab46"
)
SOURCE_PROTOCOL_VERSION = "SHORT_SWING_DEVELOPMENT_V2"
SOURCE_SESSIONS = 232
SOURCE_EVENTS = 17400
SOURCE_COHORTS = ("cohort1", "cohort2", "cohort3")
SOURCE_BLOCKS = 22

HYPOTHESIS = {
    "name": "prior_session_final_30m_momentum_spillover",
    "direction": "sign of prior session final 30-minute futures net return",
    "economic_rationale": (
        "late-session directional pressure can reflect information or positioning "
        "that is not fully resolved by the close and may spill into the next "
        "session's early trading."
    ),
}

FEATURE = {
    "source_feature": "corrected V2 net_return_6_bps",
    "source_row": "final five-minute event of prior session within same cohort",
    "same_cohort_only": True,
    "cross_cohort_carry_forbidden": True,
    "signal_known_before_next_open": True,
    "current_session_signal_row": "first five-minute event only",
    "signal_direction": "sign(prior_session_final_net_return_6_bps)",
}

PRIMARY_RULE = {
    "prior_late_30m_return_nonzero": True,
    "magnitude_threshold": None,
    "opening_gap_filter": "off",
    "current_first_bar_filter": "off",
    "volume_filter": "off",
    "OI_filter": "off",
    "options_fast_lead_filter": "off",
    "VIX_filter": "off",
    "formulations": 1,
}

EXECUTION = {
    "signal_information_cutoff": "prior session close",
    "entry": (
        "next session first event row's entry_price, the one-minute open at 09:20; "
        "a conservative fixed proxy already frozen in V2"
    ),
    "entry_delay_note": (
        "the signal is available before 09:15, but 09:20 is used to preserve exact "
        "frozen one-minute outcome comparability across all three cohorts"
    ),
    "fixed_exit_minutes": [5, 10, 15, 30],
    "one_trade_per_session": True,
    "stops": False,
    "targets": False,
    "same_bar_path_assumptions": False,
}

STRUCTURAL_GATE = {
    "minimum_pooled_trades": 150,
    "minimum_trades_each_cohort": 40,
    "mean_gross_bps_positive_each_cohort": True,
    "minimum_positive_chronological_blocks": 15,
    "total_chronological_blocks": 22,
    "pooled_session_cluster_bootstrap_95pct_lower_gt_zero": True,
    "bootstrap_seed": 20261001,
    "bootstrap_samples": 10000,
    "cost_gate": (
        "structural gate is gross; any structural pass requires a separately "
        "frozen exact-option implementation protocol before candidate status"
    ),
}

DECISION_RULE = {
    "structural_pass_action": (
        "freeze a separate exact directional option implementation protocol "
        "before inspecting option P&L"
    ),
    "no_structural_pass_action": (
        "record rejection; do not test reversal, magnitude thresholds, or gap filters"
    ),
    "candidate_freeze_at_this_stage": False,
    "blind_validation_at_this_stage": False,
}

GUARDRAILS = {
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "single_primary_rule_only": True,
    "cross_cohort_carry_forbidden": True,
    "one_trade_per_session": True,
    "no_magnitude_threshold_search": True,
    "no_opening_gap_filter": True,
    "no_current_first_bar_confirmation": True,
    "no_volume_filter": True,
    "no_OI_filter": True,
    "no_options_fast_lead_filter": True,
    "no_VIX_filter": True,
    "no_DTE_filter": True,
    "no_stop_target_search": True,
    "no_post_hoc_reversal_test": True,
    "no_prior_hypothesis_retest_or_rescue": True,
    "strategy_d_remains_paused": True,
}
