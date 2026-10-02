"""Predeclared single-bar wick-rejection reversal development protocol.

This is a distinct behavioral hypothesis from failed-breakout reversal,
close-location mean reversion, expansion-pause continuation, impulse/OI
reversal, directional-efficiency continuation, and trend-pullback continuation.

It is evaluated only on the already-inspected 152-session development corpus.
The protocol must be committed before any outcome screen is run. A structural
pass does not freeze a candidate or authorize implementation; it only permits a
separately predeclared exact-option execution study.
"""

PROTOCOL_VERSION = "SHORT_SWING_WICK_REJECTION_V1"
RESEARCH_ROLE = "INSPECTED_DEVELOPMENT_HYPOTHESIS_NOT_VALIDATION"

SOURCE = {
    "event_dataset_sha256": (
        "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f"
    ),
    "sessions": 152,
    "five_minute_events": 11400,
}

HYPOTHESIS = {
    "name": "single_bar_wick_rejection_reversal",
    "idea": (
        "A meaningful five-minute futures bar with a dominant rejection wick and "
        "a relatively small real body may reflect an attempted directional auction "
        "that was rejected before the bar closed. Price may continue briefly away "
        "from the rejected side over the next 5-30 minutes."
    ),
    "signal_bar": "current completed five-minute futures bar",
    "signal_direction": {
        "dominant_upper_wick": "short",
        "dominant_lower_wick": "long",
    },
    "measurements": {
        "bar_range": "futures_high - futures_low",
        "upper_wick": "futures_high - max(futures_open, futures_close)",
        "lower_wick": "min(futures_open, futures_close) - futures_low",
        "dominant_wick_fraction": "max(upper_wick, lower_wick) / bar_range",
        "body_fraction": "abs(futures_close - futures_open) / bar_range",
        "wick_dominance_ratio": (
            "dominant_wick / opposite_wick; infinite when opposite_wick is zero"
        ),
    },
    "fixed_wick_dominance_ratio_min": 1.50,
    "no_prior_breakout_requirement": True,
    "no_prior_pullback_requirement": True,
    "no_directional_efficiency_requirement": True,
    "no_volume_requirement": True,
    "no_oi_requirement": True,
    "no_time_or_dte_conditioning": True,
}

SEARCH_GRID = {
    "current_bar_range_percentile_min": [50],
    "dominant_wick_fraction_min": [0.45, 0.60],
    "body_fraction_max": [0.25, 0.40],
    "options_fast_lead_filter": ["off", "reversal_direction_agreement"],
    "fixed_exit_minutes": [5, 10, 15, 30],
}

THRESHOLD_POLICY = {
    "current_bar_range_percentile": (
        "derive once from all finite current-bar futures ranges in the frozen "
        "152-session inspected development corpus; threshold uses no outcome columns"
    ),
    "wick_and_body_thresholds": (
        "absolute predeclared fractions; do not optimize or add alternate wick/body "
        "definitions after outcomes are inspected"
    ),
}

EXECUTION = {
    "signal_information_cutoff": "completed five-minute rejection bar t",
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
        "A pass only opens a separately frozen exact-option implementation protocol. "
        "It does not freeze a trading candidate."
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
        "forbidden until a separate option protocol is committed after a structural "
        "gate pass and before option outcome inspection"
    ),
}

GUARDRAILS = {
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "fresh_blind_data_must_not_be_loaded": True,
    "prior_breakout_filters_forbidden": True,
    "pullback_filters_forbidden": True,
    "directional_efficiency_filters_forbidden": True,
    "prior_range_or_close_location_filters_forbidden": True,
    "volume_filters_forbidden": True,
    "oi_filters_forbidden": True,
    "time_of_day_filters_forbidden": True,
    "DTE_filters_forbidden": True,
    "stop_target_search_forbidden": True,
    "alternate_wick_definitions_forbidden": True,
    "additional_thresholds_forbidden": True,
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
