"""Predeclared option implementation screen for impulse/OI reversal.

Committed after the structural futures screen found exactly one formulation with
positive mean in both inspected development cohorts, and before exact option P&L
for that formulation is inspected.
"""

PROTOCOL_VERSION = "SHORT_SWING_IMPULSE_OI_REVERSAL_OPTIONS_V1"

STRUCTURAL_RULE = {
    "current_abs_return_percentile_min": 90,
    "derived_threshold_bps": 8.465457602550009,
    "volume_vs_prior3_mean_min": 1.2,
    "oi_change_bps_max": 0.0,
    "options_fast_lead_filter": "off",
    "direction": "opposite current five-minute futures return",
    "exit_minutes": 30,
    "overlapping_trades": False,
}

OPTION_IMPLEMENTATION = {
    "direction": "buy CE for long reversal; buy PE for short reversal",
    "strike_variants": ["ATM", "one_strike_ITM"],
    "strike_step_points": 50,
    "entry": "next five-minute option bar open after the impulse bar completes",
    "exit": "option close after fixed 30-minute holding window",
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
