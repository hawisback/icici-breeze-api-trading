"""Predeclared exact-option protocol for low-volume price-shock reversal.

Committed after the frozen futures structural screen produced exactly one pass
and before exact option P&L for this hypothesis is inspected. The structural
rule is locked to the passing p70-return / volume<=prior3-mean / 5-minute cell.

No options fast-lead filter is introduced because the structural signal passed
without one. ATM is the primary implementation; one-strike ITM is a non-rescuing
robustness check and cannot substitute for a failed ATM result.
"""

PROTOCOL_VERSION = "SHORT_SWING_LOW_VOLUME_PRICE_SHOCK_REVERSAL_OPTIONS_V1"

SOURCE = {
    "event_dataset_sha256": (
        "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f"
    ),
    "cohort1_options_sha256": (
        "5d640d90e03f4cfb6bfe7c1ba2b2a0c44525b8ab01b4ea4ac62abd350dd9991c"
    ),
    "cohort2_options_sha256": (
        "296d66947da845f3489ad97efec2b0651c0559f7b4ff0d6c486ceba05a8c98cc"
    ),
    "sessions": 152,
    "five_minute_events": 11400,
    "blind_data_used": False,
}

STRUCTURAL_RULE = {
    "current_abs_return_percentile_min": 70,
    "derived_current_abs_return_threshold_bps": 4.65280279654,
    "volume_vs_prior3_mean_max": 1.0,
    "options_fast_lead_filter": "off",
    "OI_filter": "off",
    "fixed_exit_minutes": 5,
    "signal_direction": "opposite sign of completed five-minute futures return",
    "overlapping_trades": False,
    "expected_structural_signals": 1648,
    "selection_reason": (
        "This was the only one of four predeclared structural formulations to pass "
        "the frozen trade-count, cross-cohort, chronological-block, and pooled "
        "session-cluster bootstrap gate."
    ),
}

OPTION_IMPLEMENTATION = {
    "direction": "buy CE for long reversal signal; buy PE for short reversal signal",
    "primary_strike_variant": "ATM",
    "robustness_strike_variant": "one_strike_ITM",
    "strike_step_points": 50,
    "entry": "next five-minute option bar open after the signal bar completes",
    "exit": "same five-minute option bar close",
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
    "primary_strike_variant": "ATM",
    "primary_slippage_points_per_side": 1.0,
    "require_positive_net_mean_both_cohorts_at_primary_slippage": True,
    "require_positive_pooled_net_mean_at_2_points_per_side": True,
    "minimum_positive_chronological_blocks_out_of_14_at_primary_slippage": 9,
    "require_pooled_session_cluster_bootstrap_95pct_lower_bound_gt_zero_at_primary_slippage": True,
    "robustness_strike_is_non_rescuing": True,
    "candidate_freeze_rule": (
        "ATM must satisfy every criterion. One-strike ITM is reported only as "
        "robustness context and cannot rescue a failed ATM implementation."
    ),
}

GUARDRAILS = {
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "fresh_blind_data_must_not_be_loaded": True,
    "no_other_return_thresholds": True,
    "no_other_volume_thresholds": True,
    "no_other_horizons": True,
    "no_options_fast_lead_filter": True,
    "no_OI_filter": True,
    "no_other_strikes": True,
    "no_DTE_filter": True,
    "no_time_filter": True,
    "no_VIX_filter": True,
    "no_stop_target_search": True,
    "no_post_hoc_rescue": True,
    "strategy_d_remains_paused": True,
}
