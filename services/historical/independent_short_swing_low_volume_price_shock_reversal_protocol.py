"""Predeclared low-volume price-shock reversal development protocol.

This is a volume-information hypothesis rather than another close-location or
recent-range mean-reversion screen. It asks whether a large *single-bar* futures
return that is not accompanied by futures-volume expansion is less likely to
represent durable information and therefore partially reverses.

It is also distinct from the prior impulse/OI reversal study: that study required
elevated volume and classified falling versus rising OI. This study requires
non-expanded volume and does not condition on OI. Frozen before outcome inspection.
"""

PROTOCOL_VERSION = "SHORT_SWING_LOW_VOLUME_PRICE_SHOCK_REVERSAL_V1"
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
    "name": "low_volume_price_shock_reversal",
    "mechanism": (
        "A large completed five-minute futures return without contemporaneous "
        "volume expansion versus the prior three bars may be a less durable price "
        "shock and partially reverse over the next several minutes."
    ),
    "price_feature": "current_abs_return_bps",
    "volume_feature": "futures_volume / prior-three-bar mean futures volume",
    "signal_direction": "opposite sign of current five-minute futures return",
    "actual_futures_volume_only": True,
    "same_session_volume_lookback_only": True,
}

FROZEN_RULE = {
    "current_abs_return_percentile_min": 70,
    "volume_vs_prior3_mean_max": 1.0,
    "current_return_must_be_nonzero": True,
}

THRESHOLD_POLICY = {
    "return_threshold_derive_once_from": (
        "all finite current_abs_return_bps observations in the frozen inspected corpus"
    ),
    "volume_threshold_is_absolute_ratio": True,
    "outcome_columns_forbidden": True,
    "additional_return_percentile_search_forbidden": True,
    "additional_volume_ratio_search_forbidden": True,
}

SEARCH_GRID = {
    "options_fast_lead_filter": ["off"],
    "OI_filter": ["off"],
    "fixed_exit_minutes": [5, 10, 15, 30],
    "total_formulations": 4,
}

EXECUTION = {
    "signal_information_cutoff": "completed five-minute shock bar t",
    "entry": "earliest one-minute futures open after bar t completes",
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
    "no_close_location_filter": True,
    "no_recent_range_filter": True,
    "no_breakout_filter": True,
    "no_wick_filter": True,
    "no_pullback_filter": True,
    "no_OI_filter": True,
    "no_high_volume_impulse_rule": True,
    "no_session_anchor_filter": True,
    "no_futures_spot_dislocation_filter": True,
    "no_opening_displacement_filter": True,
    "no_compression_release_filter": True,
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
    "threshold_policy": THRESHOLD_POLICY,
    "search_grid": SEARCH_GRID,
    "execution": EXECUTION,
    "structural_gate": STRUCTURAL_GATE,
    "guardrails": GUARDRAILS,
}
