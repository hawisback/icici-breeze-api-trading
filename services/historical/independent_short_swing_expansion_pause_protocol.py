"""Predeclared expansion-pause continuation development protocol.

This is a distinct behavioral hypothesis from the previously screened failed-breakout
reversal, generic breakout continuation, and close-location mean reversion families.
It is evaluated only on the already-inspected 152-session development corpus.
"""

PROTOCOL_VERSION = "SHORT_SWING_EXPANSION_PAUSE_V1"
RESEARCH_ROLE = "INSPECTED_DEVELOPMENT_HYPOTHESIS_NOT_VALIDATION"

HYPOTHESIS = {
    "name": "expansion_pause_continuation",
    "idea": (
        "A directional three-bar expansion followed by a narrow inside pause on "
        "contracting volume may continue in the expansion direction over the next "
        "5-30 minutes."
    ),
    "signal_direction": "sign of the three bars immediately preceding the pause bar",
    "setup": {
        "prior_window_bars": 3,
        "pause_bar": "current completed five-minute bar",
        "inside_pause_required": True,
        "pause_close_must_remain_in_expansion_direction_half": True,
        "volume_contraction_required": True,
    },
}

SEARCH_GRID = {
    "prior_3_abs_net_return_percentile_min": [70, 80],
    "pause_range_divided_by_prior_3_range_max": [0.33, 0.50],
    "pause_volume_divided_by_prior_3_mean_max": [1.0],
    "options_fast_lead_filter": ["off", "directional_agreement"],
    "fixed_exit_minutes": [5, 10, 15, 30],
}

EXECUTION = {
    "entry": "earliest one-minute open after the pause bar completes",
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
    ],
    "futures_cost_screen_before_stop_target_work": True,
    "options_implementation_only_if_structural_futures_screen_is_cross_cohort_positive": True,
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
    "execution": EXECUTION,
    "evaluation": EVALUATION,
    "guardrails": GUARDRAILS,
}
