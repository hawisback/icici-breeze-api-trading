"""Predeclared session-anchor deviation reversion development protocol.

This is a genuinely distinct anchor-based hypothesis, not a rescue of the
rejected futures-spot dislocation signal. It uses actual futures volume and a
cumulative same-session volume-weighted typical-price anchor computed only from
completed five-minute bars.
"""

PROTOCOL_VERSION = "SHORT_SWING_SESSION_ANCHOR_REVERSION_V1"

SOURCE = {
    "event_dataset_sha256": (
        "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f"
    ),
    "sessions": 152,
    "five_minute_events": 11400,
    "blind_data_used": False,
}

HYPOTHESIS = {
    "name": "session_volume_weighted_anchor_deviation_reversion",
    "mechanism": (
        "When the completed futures close is unusually far from the cumulative "
        "same-session volume-weighted typical-price anchor, short-horizon liquidity "
        "and inventory effects may pull price back toward that anchor."
    ),
    "typical_price": "(futures_high + futures_low + futures_close) / 3",
    "anchor": (
        "cumulative sum(typical_price * futures_volume) / cumulative "
        "sum(futures_volume), reset each session and including the completed signal bar"
    ),
    "deviation_bps": "(futures_close / session_anchor - 1) * 10000",
    "direction": "opposite sign of deviation_bps",
    "actual_futures_volume_only": True,
}

SEARCH_GRID = {
    "abs_anchor_deviation_percentile_min": [80, 90],
    "options_fast_lead_filter": ["off", "reversion_direction_agreement"],
    "fixed_exit_minutes": [5, 10, 15, 30],
    "total_formulations": 16,
}

THRESHOLD_POLICY = {
    "derive_once_from": (
        "all finite absolute anchor deviations in the frozen inspected 152-session corpus"
    ),
    "outcome_columns_forbidden": True,
    "additional_threshold_search_forbidden": True,
}

EXECUTION = {
    "signal_information_cutoff": "completed five-minute bar",
    "entry": "earliest one-minute futures open after the completed signal bar",
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
    "no_futures_spot_dislocation_filter": True,
    "no_breakout_filter": True,
    "no_pullback_filter": True,
    "no_wick_filter": True,
    "no_directional_efficiency_filter": True,
    "no_OI_filter": True,
    "no_relative_volume_threshold": True,
    "no_VIX_filter": True,
    "no_time_filter": True,
    "no_DTE_filter": True,
    "no_stop_target_search": True,
    "no_post_hoc_rescue": True,
    "exact_option_pnl_forbidden_until_structural_pass_and_separate_protocol": True,
    "strategy_d_remains_paused": True,
}
