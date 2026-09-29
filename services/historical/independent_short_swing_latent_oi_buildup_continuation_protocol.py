"""Predeclared latent OI build-up continuation development protocol.

This hypothesis is distinct from the prior impulse/OI reversal study. The prior
study began with a large price impulse on elevated volume and asked whether
falling OI implied reversal. This study begins with unusually large *positive*
OI change while contemporaneous price displacement is deliberately modest and
asks whether the small price direction subsequently continues.

Frozen before outcome inspection.
"""

PROTOCOL_VERSION = "SHORT_SWING_LATENT_OI_BUILDUP_CONTINUATION_V1"
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
    "name": "latent_oi_buildup_continuation",
    "mechanism": (
        "A large same-contract increase in futures open interest accompanied by "
        "only a modest contemporaneous price move may represent fresh position "
        "building before price has fully adjusted. The sign of the modest current "
        "futures return supplies direction for a short-horizon continuation test."
    ),
    "oi_feature": "current five-minute futures oi_change_bps",
    "price_feature": "current five-minute futures_return_bps",
    "signal_direction": "sign of current five-minute futures return",
    "same_session_features_only": True,
    "actual_futures_open_interest_only": True,
}

FROZEN_RULE = {
    "oi_change_percentile_min": 80,
    "current_abs_return_percentile_max": 50,
    "oi_change_must_be_positive": True,
    "current_return_must_be_nonzero": True,
}

THRESHOLD_POLICY = {
    "oi_threshold_derive_once_from": (
        "all finite positive oi_change_bps observations in the frozen inspected corpus"
    ),
    "price_threshold_derive_once_from": (
        "all finite current_abs_return_bps observations in the frozen inspected corpus"
    ),
    "outcome_columns_forbidden": True,
    "additional_percentile_search_forbidden": True,
}

SEARCH_GRID = {
    "options_fast_lead_filter": ["off"],
    "volume_filter": ["off"],
    "fixed_exit_minutes": [5, 10, 15, 30],
    "total_formulations": 4,
}

EXECUTION = {
    "signal_information_cutoff": "completed five-minute bar t",
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
    "no_large_price_impulse_filter": True,
    "no_falling_OI_reversal_rule": True,
    "no_volume_filter": True,
    "no_breakout_filter": True,
    "no_pullback_filter": True,
    "no_wick_filter": True,
    "no_directional_efficiency_filter": True,
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
