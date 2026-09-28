"""Predeclared impulse/OI non-confirmation reversal development protocol.

This is a distinct behavioral hypothesis from failed-breakout reversal,
close-location mean reversion, generic breakout continuation, and
expansion-pause continuation. It is evaluated only on the already-inspected
152-session development corpus.
"""

PROTOCOL_VERSION = "SHORT_SWING_IMPULSE_OI_REVERSAL_V1"
RESEARCH_ROLE = "INSPECTED_DEVELOPMENT_HYPOTHESIS_NOT_VALIDATION"

HYPOTHESIS = {
    "name": "impulse_oi_nonconfirmation_reversal",
    "idea": (
        "A large one-bar futures price displacement on elevated volume, when "
        "open interest falls rather than expands, may represent covering or "
        "liquidation rather than fresh position building and may reverse over "
        "the next 5-30 minutes."
    ),
    "signal_direction": "opposite the sign of the current five-minute futures return",
    "required_state": {
        "oi_nonconfirmation": "current five-minute OI change <= 0 bps",
        "volume_expansion": "current futures volume divided by prior-three-bar mean",
    },
}

SEARCH_GRID = {
    "current_abs_return_percentile_min": [80, 90],
    "volume_vs_prior3_mean_min": [1.2, 1.5],
    "options_fast_lead_filter": ["off", "agrees_with_reversal"],
    "fixed_exit_minutes": [5, 10, 15, 30],
}

CONTROL_COMPARISON = {
    "same_price_and_volume_grid_with_oi_change_gt_0": (
        "descriptive control only; it cannot be promoted under this hypothesis"
    ),
}

EXECUTION = {
    "entry": "earliest one-minute open after the impulse bar completes",
    "overlapping_trades": False,
    "stop_target_optimization": False,
    "same_bar_path_assumptions": False,
}

EVALUATION = {
    "require_both_cohorts_same_sign_before_implementation_research": True,
    "report": [
        "trades",
        "sessions_traded",
        "trades_per_session",
        "pooled_mean_bps",
        "cohort1_mean_bps",
        "cohort2_mean_bps",
        "win_rate",
        "MFE",
        "MAE",
        "chronological_block_means",
        "session_cluster_bootstrap_mean_95pct",
    ],
    "futures_cost_screen_before_option_implementation": True,
    "option_implementation_only_if_structural_futures_screen_is_cross_cohort_positive": True,
}

GUARDRAILS = {
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "fresh_blind_data_must_not_be_loaded": True,
    "failed_breakout_filters_forbidden": True,
    "time_of_day_filters_forbidden": True,
    "DTE_filters_forbidden": True,
    "stop_target_search_forbidden": True,
    "post_hoc_rescue_forbidden": True,
    "strategy_d_remains_paused": True,
}

PROTOCOL = {
    "protocol_version": PROTOCOL_VERSION,
    "research_role": RESEARCH_ROLE,
    "hypothesis": HYPOTHESIS,
    "search_grid": SEARCH_GRID,
    "control_comparison": CONTROL_COMPARISON,
    "execution": EXECUTION,
    "evaluation": EVALUATION,
    "guardrails": GUARDRAILS,
}
