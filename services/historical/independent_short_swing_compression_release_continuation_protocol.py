"""Predeclared compression-release continuation development protocol.

This is a volatility-regime-transition hypothesis, distinct from generic breakout
continuation, expansion-pause continuation, directional-efficiency continuation,
trend-pullback continuation, wick rejection, session-anchor reversion, futures-
spot dislocation, and opening-displacement fade.

The setup does not require a breakout of a price level. It requires three prior
bars to contract relative to the preceding three bars, followed by a current
five-minute expansion bar that closes directionally near one extreme. The
expansion bar supplies direction. Frozen before outcome inspection.
"""

PROTOCOL_VERSION = "SHORT_SWING_COMPRESSION_RELEASE_CONTINUATION_V1"
RESEARCH_ROLE = "INSPECTED_DEVELOPMENT_HYPOTHESIS_NOT_VALIDATION"

SOURCE = {
    "event_dataset_sha256": (
        "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f"
    ),
    "sessions": 152,
    "five_minute_events": 11400,
    "blind_data_used": False,
}

HYPOTHESIS = {
    "name": "compression_release_continuation",
    "mechanism": (
        "A short volatility contraction can represent temporary balance. If the "
        "next completed five-minute bar expands sharply and closes near an extreme "
        "in the same direction as its own body, the release may contain persistent "
        "order flow that continues briefly."
    ),
    "prior_compression_window": "bars t-3 through t-1",
    "comparison_window": "bars t-6 through t-4",
    "compression_ratio": (
        "mean range_bps(t-3..t-1) / mean range_bps(t-6..t-4)"
    ),
    "release_expansion_ratio": (
        "current bar range_bps / mean range_bps(t-3..t-1)"
    ),
    "current_close_location": "(close-low)/(high-low)",
    "signal_direction": (
        "long when current body is positive and close_location >= 0.75; "
        "short when current body is negative and close_location <= 0.25"
    ),
    "no_price_level_breakout_requirement": True,
    "same_session_lookbacks_only": True,
}

FROZEN_RULE = {
    "compression_ratio_max": 0.75,
    "release_expansion_ratio_min": 1.50,
    "long_close_location_min": 0.75,
    "short_close_location_max": 0.25,
    "current_body_must_agree_with_direction": True,
}

SEARCH_GRID = {
    "options_fast_lead_filter": ["off"],
    "fixed_exit_minutes": [5, 10, 15, 30],
    "total_formulations": 4,
}

THRESHOLD_POLICY = {
    "absolute_ratios_only": True,
    "percentile_search": False,
    "outcome_columns_forbidden": True,
    "additional_ratio_search_forbidden": True,
}

EXECUTION = {
    "signal_information_cutoff": "completed five-minute release bar t",
    "entry": "earliest one-minute futures open after bar t completes",
    "overlapping_trades": False,
    "overlap_rule": (
        "within each horizon and session, accept the earliest signal then skip "
        "signals whose entry occurs before the accepted trade's fixed exit"
    ),
    "fixed_exits_only": [5, 10, 15, 30],
    "stop_target_optimization": False,
    "same_bar_path_assumptions": False,
}

STRUCTURAL_GATE = {
    "minimum_pooled_trades": 100,
    "minimum_trades_per_cohort": 40,
    "require_positive_mean_in_both_cohorts": True,
    "minimum_positive_chronological_blocks_out_of_14": 9,
    "require_pooled_session_cluster_bootstrap_95pct_lower_bound_gt_zero": True,
}

GUARDRAILS = {
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "fresh_blind_data_must_not_be_loaded": True,
    "no_price_level_breakout_filter": True,
    "no_inside_pause_filter": True,
    "no_pullback_filter": True,
    "no_wick_reversal_filter": True,
    "no_directional_efficiency_filter": True,
    "no_session_anchor_filter": True,
    "no_futures_spot_dislocation_filter": True,
    "no_opening_displacement_filter": True,
    "no_volume_filter": True,
    "no_OI_filter": True,
    "no_VIX_filter": True,
    "no_options_fast_lead_filter": True,
    "no_time_filter": True,
    "no_DTE_filter": True,
    "no_stop_target_search": True,
    "no_post_hoc_rescue": True,
    "exact_option_pnl_forbidden_until_structural_pass_and_separate_protocol": True,
    "strategy_d_remains_paused": True,
}

PROTOCOL = {
    "protocol_version": PROTOCOL_VERSION,
    "research_role": RESEARCH_ROLE,
    "source": SOURCE,
    "hypothesis": HYPOTHESIS,
    "frozen_rule": FROZEN_RULE,
    "search_grid": SEARCH_GRID,
    "threshold_policy": THRESHOLD_POLICY,
    "execution": EXECUTION,
    "structural_gate": STRUCTURAL_GATE,
    "guardrails": GUARDRAILS,
}
