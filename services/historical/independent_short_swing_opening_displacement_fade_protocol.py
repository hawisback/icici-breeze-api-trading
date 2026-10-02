"""Predeclared development protocol for NIFTY opening-displacement fade.

This is a genuinely distinct hypothesis from session-anchor reversion and
futures-spot return dislocation. The signal is session-level and uses NIFTY spot
to avoid futures contract-roll contamination: compare the first completed
five-minute spot close with the prior session's final spot close, then test a
short-horizon futures fade of that opening displacement.

Frozen before outcome inspection.
"""

PROTOCOL_VERSION = "SHORT_SWING_OPENING_DISPLACEMENT_FADE_V1"

SOURCE = {
    "event_dataset_sha256": (
        "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f"
    ),
    "sessions": 152,
    "five_minute_events": 11400,
    "blind_data_used": False,
}

HYPOTHESIS = {
    "name": "spot_opening_displacement_short_horizon_fade",
    "mechanism": (
        "After overnight price discovery and the first completed five-minute bar, "
        "a non-trivial displacement of NIFTY spot from the prior session close may "
        "contain a temporary opening overreaction that partially fades over the "
        "next several minutes."
    ),
    "opening_reference": (
        "first completed five-minute spot close divided by the immediately prior "
        "session's final spot close, calculated within cohort so the gap between "
        "the two inspected cohorts is never bridged"
    ),
    "opening_displacement_bps": (
        "(first_session_spot_close / prior_session_final_spot_close - 1) * 10000"
    ),
    "direction": "opposite sign of opening_displacement_bps",
    "one_signal_opportunity_per_session": True,
    "spot_reference_avoids_futures_roll_gap": True,
}

SEARCH_GRID = {
    "abs_opening_displacement_percentile_min": [25],
    "options_fast_lead_filter": ["off"],
    "fixed_exit_minutes": [5, 10, 15, 30],
    "total_formulations": 4,
}

THRESHOLD_POLICY = {
    "derive_once_from": (
        "all finite absolute opening displacements from eligible inspected sessions"
    ),
    "outcome_columns_forbidden": True,
    "additional_threshold_search_forbidden": True,
    "reason_for_p25": (
        "With only one opening opportunity per session, p25 retains roughly three "
        "quarters of eligible sessions so the unchanged structural trade-count gate "
        "remains capable of passing; no p50/p80/p90 tail search is permitted."
    ),
}

EXECUTION = {
    "signal_information_cutoff": "first completed five-minute bar of each session",
    "entry": "earliest one-minute futures open after that completed first bar",
    "overlapping_trades": False,
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
    "first_completed_bar_only": True,
    "no_session_anchor_filter": True,
    "no_futures_spot_dislocation_filter": True,
    "no_breakout_filter": True,
    "no_pullback_filter": True,
    "no_wick_filter": True,
    "no_directional_efficiency_filter": True,
    "no_OI_filter": True,
    "no_relative_volume_threshold": True,
    "no_VIX_filter": True,
    "no_time_search": True,
    "no_DTE_filter": True,
    "no_options_fast_lead_filter": True,
    "no_stop_target_search": True,
    "no_post_hoc_rescue": True,
    "exact_option_pnl_forbidden_until_structural_pass_and_separate_protocol": True,
    "strategy_d_remains_paused": True,
}
