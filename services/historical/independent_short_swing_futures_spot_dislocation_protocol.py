"""Predeclared futures-spot return-dislocation reversion development protocol.

This is a distinct cross-market microstructure hypothesis from the previously
screened price-pattern, breakout, pullback, wick, OI, and directional-efficiency
families. It is evaluated only on the already-inspected 152-session development
corpus.

The protocol must be committed before any outcome screen for this hypothesis is
run. A structural pass does not freeze a candidate or authorize implementation;
it only permits a separately predeclared exact-option execution study.
"""

PROTOCOL_VERSION = "SHORT_SWING_FUTURES_SPOT_DISLOCATION_V1"
RESEARCH_ROLE = "INSPECTED_DEVELOPMENT_HYPOTHESIS_NOT_VALIDATION"

SOURCE = {
    "event_dataset_sha256": (
        "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f"
    ),
    "sessions": 152,
    "five_minute_events": 11400,
}

HYPOTHESIS = {
    "name": "futures_spot_return_dislocation_reversion",
    "idea": (
        "NIFTY futures and NIFTY spot should remain tightly linked intraday. "
        "When the completed five-minute futures close-to-close return materially "
        "outruns the simultaneous spot close-to-close return, the relative move "
        "may contain a temporary futures-specific dislocation that partially "
        "reverts over the next 5-30 minutes."
    ),
    "measurement": {
        "futures_return_bps": "current futures close / prior same-session futures close - 1",
        "spot_return_bps": "current spot close / prior same-session spot close - 1",
        "return_dislocation_bps": "futures_return_bps - spot_return_bps",
    },
    "signal_direction": (
        "opposite sign of return_dislocation_bps: short futures when futures "
        "outperform spot; long futures when futures underperform spot"
    ),
    "same_session_returns_only": True,
    "no_absolute_price_basis_filter": True,
    "no_breakout_requirement": True,
    "no_pullback_requirement": True,
    "no_wick_requirement": True,
    "no_directional_efficiency_requirement": True,
    "no_volume_requirement": True,
    "no_oi_requirement": True,
    "no_time_or_dte_conditioning": True,
}

SEARCH_GRID = {
    "abs_return_dislocation_percentile_min": [80, 90],
    "options_fast_lead_filter": ["off", "reversion_direction_agreement"],
    "fixed_exit_minutes": [5, 10, 15, 30],
}

THRESHOLD_POLICY = {
    "abs_return_dislocation_percentiles": (
        "derive once from all finite abs(futures_return_bps - spot_return_bps) "
        "values in the frozen 152-session inspected development corpus; thresholds "
        "use no outcome columns"
    ),
    "no_additional_dislocation_thresholds": True,
}

EXECUTION = {
    "signal_information_cutoff": "completed five-minute futures and spot bar t",
    "entry": "earliest one-minute futures open after bar t completes",
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
        "mean_abs_return_dislocation_bps",
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
    "absolute_basis_filters_forbidden": True,
    "breakout_filters_forbidden": True,
    "pullback_filters_forbidden": True,
    "wick_filters_forbidden": True,
    "directional_efficiency_filters_forbidden": True,
    "prior_range_or_close_location_filters_forbidden": True,
    "volume_filters_forbidden": True,
    "oi_filters_forbidden": True,
    "time_of_day_filters_forbidden": True,
    "DTE_filters_forbidden": True,
    "stop_target_search_forbidden": True,
    "additional_thresholds_forbidden": True,
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
