"""Frozen protocol for session VWAP cross-through continuation.

Hypothesis
----------
A completed five-minute NIFTY futures bar that crosses the evolving session
volume-weighted benchmark may mark acceptance on the new side of that benchmark,
with short-horizon continuation in the crossing direction.

Important semantic limitation:
The source contains five-minute OHLCV, not tick-by-tick trades. Therefore the
benchmark is explicitly a five-minute bar-volume-weighted session VWAP proxy,
using each completed bar's typical price ((high + low + close) / 3) weighted by
actual futures traded volume. It must not be described as exact exchange VWAP.

This mechanism is distinct from prior session-anchor deviation reversion because
the anchor here evolves with actual futures volume and the event is a cross-
through/acceptance signal, not a static-anchor deviation fade.

The rule is frozen before outcomes are inspected.
"""
from __future__ import annotations

PROTOCOL_VERSION = "SHORT_SWING_SESSION_VWAP_CROSS_CONTINUATION_V1"
SOURCE_EVENT_SHA256 = (
    "e4c08b2bbf8488c9a5bd1fd6229b242f6ce9f0e27060c2cce16b03886724ab46"
)
SOURCE_PROTOCOL_VERSION = "SHORT_SWING_DEVELOPMENT_V2"
SOURCE_SESSIONS = 232
SOURCE_EVENTS = 17400
SOURCE_COHORTS = ("cohort1", "cohort2", "cohort3")
SOURCE_BLOCKS = 22

HYPOTHESIS = {
    "name": "session_vwap_cross_through_continuation",
    "direction": "direction of the completed-bar cross through session VWAP proxy",
    "economic_rationale": (
        "crossing an evolving volume-weighted session benchmark may reflect "
        "acceptance of price on the new side of the benchmark and short-horizon "
        "follow-through from benchmark-sensitive intraday flow."
    ),
}

FEATURE = {
    "price_input": "five-minute futures high/low/close",
    "volume_input": "actual futures traded volume only",
    "bar_typical_price": "(futures_high + futures_low + futures_close) / 3",
    "session_vwap_proxy": (
        "same-session cumulative sum(bar_typical_price * futures_volume) / "
        "cumulative sum(futures_volume)"
    ),
    "same_session_only": True,
    "minimum_completed_bars": 6,
    "minimum_history_interpretation": (
        "one completed 30-minute baseline before a crossing event is eligible; "
        "this is fixed a priori and is not searched"
    ),
    "cross_up": (
        "previous close < previous session_vwap_proxy and "
        "current close > current session_vwap_proxy"
    ),
    "cross_down": (
        "previous close > previous session_vwap_proxy and "
        "current close < current session_vwap_proxy"
    ),
}

PRIMARY_RULE = {
    "cross_required": True,
    "minimum_completed_bars": 6,
    "signal_direction": "+1 for cross_up, -1 for cross_down",
    "distance_threshold": None,
    "volume_threshold": None,
    "options_fast_lead_filter": "off",
    "OI_filter": "off",
    "VIX_filter": "off",
    "formulations": 1,
}

EXECUTION = {
    "signal_information_cutoff": "completed five-minute bar labelled t",
    "entry": "one-minute futures open at t+5 minutes",
    "fixed_exit_minutes": [5, 10, 15, 30],
    "non_overlapping_trades_within_each_horizon": True,
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
    "bootstrap_seed": 20260930,
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
    "no_structural_pass_action": "record rejection; no rescue or alternate rule",
    "candidate_freeze_at_this_stage": False,
    "blind_validation_at_this_stage": False,
}

GUARDRAILS = {
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "single_primary_rule_only": True,
    "bar_vwap_proxy_not_exact_tick_vwap": True,
    "no_alternate_minimum_history": True,
    "no_distance_threshold_search": True,
    "no_volume_threshold_search": True,
    "no_options_fast_lead_filter": True,
    "no_OI_filter": True,
    "no_VIX_filter": True,
    "no_time_of_day_filter": True,
    "no_DTE_filter": True,
    "no_stop_target_search": True,
    "no_post_hoc_direction_flip": True,
    "no_prior_hypothesis_retest_or_rescue": True,
    "strategy_d_remains_paused": True,
}
