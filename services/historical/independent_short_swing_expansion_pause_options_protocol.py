"""Predeclared option implementation screen for expansion-pause continuation.

This addendum is committed after the futures structural screen showed the same
positive sign in both development cohorts, and before exact option P&L is inspected.
"""

PROTOCOL_VERSION = "SHORT_SWING_EXPANSION_PAUSE_OPTIONS_V1"

STRUCTURAL_RULE = {
    "prior_3_abs_net_return_percentile_min": 70,
    "pause_range_divided_by_prior_3_range_max": 0.33,
    "pause_volume_divided_by_prior_3_mean_max": 1.0,
    "inside_pause_required": True,
    "pause_close_must_remain_in_expansion_direction_half": True,
    "options_fast_lead_directional_agreement": True,
    "exit_minutes": 10,
    "overlapping_trades": False,
}

OPTION_IMPLEMENTATION = {
    "direction": "buy CE for positive expansion direction; buy PE for negative",
    "strike_variants": ["ATM", "one_strike_ITM"],
    "strike_step_points": 50,
    "entry": "next five-minute option bar open after the pause bar completes",
    "exit": "option close after fixed 10-minute holding window",
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

GUARDRAILS = {
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "fresh_blind_data_must_not_be_loaded": True,
    "no_other_strikes": True,
    "no_DTE_filter": True,
    "no_time_filter": True,
    "no_stop_target_search": True,
    "no_post_hoc_rescue": True,
}
