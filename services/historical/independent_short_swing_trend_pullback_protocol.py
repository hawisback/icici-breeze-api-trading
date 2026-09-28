"""Predeclared trend-pullback continuation development protocol.

This is a distinct behavioral hypothesis from generic breakout continuation,
failed-breakout reversal, expansion-pause continuation, directional-efficiency
continuation, close-location mean reversion, and impulse/OI reversal.

It is evaluated only on the already-inspected 152-session development corpus.
The protocol must be committed before any outcome screen for this hypothesis is
run. A structural pass does not freeze a candidate or authorize implementation;
it only permits a separately predeclared exact-option execution study.
"""

PROTOCOL_VERSION = "SHORT_SWING_TREND_PULLBACK_V1"
RESEARCH_ROLE = "INSPECTED_DEVELOPMENT_HYPOTHESIS_NOT_VALIDATION"

SOURCE = {
    "event_dataset_sha256": (
        "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f"
    ),
    "sessions": 152,
    "five_minute_events": 11400,
}

HYPOTHESIS = {
    "name": "trend_pullback_continuation",
    "idea": (
        "A substantial three-bar directional impulse followed by a genuine but "
        "partial three-bar countertrend retracement may preserve the original "
        "order-flow direction and resume over the next 5-30 minutes."
    ),
    "six_bar_structure": {
        "impulse_leg": "bars t-5 through t-3",
        "pullback_leg": "bars t-2 through t",
        "signal_direction": "sign of the impulse-leg return",
        "pullback_required": (
            "pullback-leg return must have the opposite sign to the impulse leg"
        ),
        "retracement_fraction": (
            "abs(pullback_leg_return_bps) / abs(impulse_leg_return_bps)"
        ),
    },
    "no_breakout_requirement": True,
    "no_pause_or_inside_bar_requirement": True,
    "no_directional_efficiency_requirement": True,
    "no_volume_requirement": True,
    "no_oi_requirement": True,
    "no_range_requirement": True,
    "no_time_or_dte_conditioning": True,
}

SEARCH_GRID = {
    "impulse_abs_return_percentile_min": [70, 80],
    "pullback_fraction_min": [0.20],
    "pullback_fraction_max": [0.50, 0.75],
    "options_fast_lead_filter": ["off", "original_direction_agreement"],
    "fixed_exit_minutes": [5, 10, 15, 30],
}

THRESHOLD_POLICY = {
    "impulse_abs_return_percentiles": (
        "derive once from all finite absolute impulse-leg returns in the frozen "
        "152-session inspected development corpus; thresholds use no outcome columns"
    ),
    "pullback_fraction_bounds": (
        "absolute predeclared ratios; the 0.20 minimum ensures a real countertrend "
        "move rather than relabeling an expansion-pause setup"
    ),
}

EXECUTION = {
    "signal_information_cutoff": "completed pullback bar t",
    "entry": "earliest one-minute open after bar t completes",
    "overlapping_trades": False,
    "overlap_rule": (
        "within each formulation and session, accept the earliest signal then skip "
        "signals whose entry timestamp occurs before the accepted trade's fixed exit"
    ),
    "stop_target_optimization": False,
    "same_bar_path_assumptions": False,
}

STRUCTURAL_GATE = {
    "minimum_pooled_trades": 100,
    "minimum_trades_per_cohort": 40,
    "require_positive_mean_in_both_cohorts": True,
    "minimum_positive_chronological_blocks_out_of_14": 9,
    "require_pooled_session_cluster_bootstrap_95pct_lower_bound_gt_zero": True,
    "note": (
        "A pass only opens a separately frozen exact-option implementation "
        "protocol. It does not freeze a trading candidate."
    ),
}

EVALUATION = {
    "report_all_formulations": True,
    "report": [
        "trades",
        "trades_by_cohort",
        "sessions_traded",
        "trades_per_session",
        "pooled_mean_bps",
        "pooled_median_bps",
        "cohort1_mean_bps",
        "cohort2_mean_bps",
        "win_rate",
        "mean_MFE_bps",
        "mean_MAE_bps",
        "chronological_block_means",
        "session_cluster_bootstrap_mean_95pct",
        "entry_gap_bps",
    ],
    "futures_cost_screen": True,
    "exact_option_implementation": (
        "forbidden until a separate option protocol is committed after a "
        "structural gate pass and before option outcome inspection"
    ),
}

GUARDRAILS = {
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "fresh_blind_data_must_not_be_loaded": True,
    "directional_efficiency_filters_forbidden": True,
    "breakout_filters_forbidden": True,
    "failed_breakout_filters_forbidden": True,
    "pause_or_inside_bar_filters_forbidden": True,
    "range_filters_forbidden": True,
    "volume_filters_forbidden": True,
    "oi_filters_forbidden": True,
    "time_of_day_filters_forbidden": True,
    "DTE_filters_forbidden": True,
    "stop_target_search_forbidden": True,
    "additional_pullback_bounds_forbidden": True,
    "additional_lookbacks_forbidden": True,
    "post_hoc_rescue_forbidden": True,
    "strategy_d_remains_paused": True,
}

PROTOCOL = {
    "protocol_version": PROTOCOL_VERSION,
    "research_role": RESEARCH_ROLE,
    "source": SOURCE,
    "hypothesis": HYPOTHESIS,
    "search_grid": SEARCH_GRID,
    "threshold_policy": THRESHOLD_POLICY,
    "execution": EXECUTION,
    "structural_gate": STRUCTURAL_GATE,
    "evaluation": EVALUATION,
    "guardrails": GUARDRAILS,
}
