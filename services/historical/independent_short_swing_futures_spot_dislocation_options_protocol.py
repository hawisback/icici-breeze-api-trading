"""Predeclared exact-option implementation protocol for futures-spot dislocation reversion.

Committed only after the frozen futures structural screen passed and before any
exact option P&L for this hypothesis is inspected. This addendum deliberately
preserves the robust p80/p90 x 5m/10m neighborhood rather than selecting a
single best development cell.
"""

PROTOCOL_VERSION = "SHORT_SWING_FUTURES_SPOT_DISLOCATION_OPTIONS_V1"

SOURCE = {
    "event_dataset_sha256": (
        "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f"
    ),
    "sessions": 152,
    "five_minute_events": 11400,
    "blind_data_used": False,
}

STRUCTURAL_RULE_FAMILY = {
    "abs_return_dislocation_percentile_min": [80, 90],
    "options_fast_lead_filter": "reversion_direction_agreement",
    "fixed_exit_minutes": [5, 10],
    "selection_reason": (
        "All four cells were predeclared in the futures structural protocol and "
        "all four passed the structural gate. Keeping the full neighboring "
        "p80/p90 x 5m/10m family avoids choosing a single best cell after seeing "
        "development outcomes."
    ),
    "signal_direction": (
        "opposite sign of futures_return_bps minus same-session spot_return_bps"
    ),
    "overlapping_trades": False,
}

OPTION_IMPLEMENTATION = {
    "direction": "buy CE for long-futures reversion signal; buy PE for short-futures reversion signal",
    "strike_variants": ["ATM", "one_strike_ITM"],
    "strike_step_points": 50,
    "entry": "next five-minute option bar open after the signal bar completes",
    "exit": "option close after the formulation's fixed 5- or 10-minute holding window",
    "exact_contract_only": True,
    "contract_stitching": False,
    "slippage_points_per_side": [0.0, 1.0, 2.0],
    "stop_target_optimization": False,
}

COST_MODEL = {
    "reuse": "SHORT_SWING_DEVELOPMENT_FINDINGS_V1.current_cost_assumptions",
    "lot_size": 65,
    "brokerage_per_order_rupees": 20.0,
    "options_stt_sell_premium_rate": 0.0015,
    "options_exchange_transaction_rate_each_side": 0.0003553,
    "sebi_turnover_rate_each_side": 0.000001,
    "options_stamp_buy_rate": 0.00003,
    "gst_rate": 0.18,
}

PASS_CRITERIA = {
    "primary_slippage_points_per_side": 1.0,
    "require_positive_net_mean_both_cohorts": True,
    "require_positive_pooled_net_mean_at_2_points_per_side": True,
    "minimum_positive_chronological_blocks_out_of_14_at_primary_slippage": 9,
    "require_pooled_session_cluster_bootstrap_95pct_lower_bound_gt_zero_at_primary_slippage": True,
    "neighborhood_requirement": (
        "At least three of the four predeclared p80/p90 x 5m/10m cells must pass "
        "for the same strike variant; no single-cell promotion."
    ),
}

GUARDRAILS = {
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "fresh_blind_data_must_not_be_loaded": True,
    "no_other_thresholds": True,
    "no_other_horizons": True,
    "no_other_strikes": True,
    "no_DTE_filter": True,
    "no_time_filter": True,
    "no_volume_filter": True,
    "no_OI_filter": True,
    "no_stop_target_search": True,
    "no_post_hoc_rescue": True,
    "strategy_d_remains_paused": True,
}
