"""Predeclared directional-efficiency continuation development protocol.

This is a distinct behavioral hypothesis from failed-breakout reversal, generic
breakout continuation, expansion-pause continuation, close-location mean
reversion, and impulse/OI reversal. It is evaluated only on the already-inspected
152-session development corpus.

The protocol must be committed before any outcome screen for this hypothesis is
run. Passing the structural screen does not freeze a candidate or authorize
implementation; it only permits a separately predeclared option-execution study.
"""

PROTOCOL_VERSION = "SHORT_SWING_DIRECTIONAL_EFFICIENCY_V1"
RESEARCH_ROLE = "INSPECTED_DEVELOPMENT_HYPOTHESIS_NOT_VALIDATION"

SOURCE = {
    "event_dataset_sha256": (
        "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f"
    ),
    "sessions": 152,
    "five_minute_events": 11400,
}

HYPOTHESIS = {
    "name": "directional_efficiency_continuation",
    "idea": (
        "A substantial three-bar futures move that travels mostly in one direction, "
        "rather than accumulating the same net displacement through a choppy path, "
        "may reflect persistent order flow and continue briefly over the next "
        "5-30 minutes."
    ),
    "signal_direction": "sign of net_return_3_bps",
    "directional_efficiency": (
        "abs(net_return_3_bps) / path_length_3_bps, clipped to [0, 1]"
    ),
    "lookback_bars": 3,
    "no_breakout_requirement": True,
    "no_pause_requirement": True,
    "no_oi_requirement": True,
    "no_time_or_dte_conditioning": True,
}

SEARCH_GRID = {
    "abs_net_return_3_percentile_min": [60, 75],
    "directional_efficiency_min": [0.60, 0.80],
    "options_fast_lead_filter": ["off", "directional_agreement"],
    "fixed_exit_minutes": [5, 10, 15, 30],
}

THRESHOLD_POLICY = {
    "abs_net_return_percentiles": (
        "derive once from all finite abs(net_return_3_bps) values in the frozen "
        "152-session inspected development corpus; thresholds use no outcome columns"
    ),
    "efficiency_thresholds": "absolute predeclared ratios; do not optimize",
}

CONTROL_COMPARISON = {
    "low_efficiency_max": 0.40,
    "description": (
        "For each absolute-net-return percentile and exit horizon, report a "
        "descriptive low-efficiency control with efficiency <= 0.40 and no options "
        "filter. The control cannot be promoted under this hypothesis."
    ),
}

EXECUTION = {
    "entry": "earliest one-minute open after the third completed five-minute bar",
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
        "This gate is intentionally conservative because the grid contains multiple "
        "development formulations. A pass only opens a separately frozen option "
        "implementation protocol; it does not freeze a trading candidate."
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
    "breakout_filters_forbidden": True,
    "failed_breakout_filters_forbidden": True,
    "pause_filters_forbidden": True,
    "oi_filters_forbidden": True,
    "volume_filters_forbidden": True,
    "time_of_day_filters_forbidden": True,
    "DTE_filters_forbidden": True,
    "stop_target_search_forbidden": True,
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
    "control_comparison": CONTROL_COMPARISON,
    "execution": EXECUTION,
    "structural_gate": STRUCTURAL_GATE,
    "evaluation": EVALUATION,
    "guardrails": GUARDRAILS,
}
