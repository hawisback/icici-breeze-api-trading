"""Frozen F5 pre-trail RVI signal-failure exit diagnostic.

Purpose
-------
Try to reduce failed setups without using a fixed price stop and without
changing any trade after the existing +10% trail activation.

Candidate
---------
Before trail activation only:
- if the held option's Relative Volatility Index(10) is below 50 on TWO
  consecutive completed 2-minute bars, exit at the next 2-minute bar open.
- a single sub-50 bar does not exit.
- if the +10% trail activation occurs on the same completed bar, trail
  activation has precedence and the RVI failure rule is disabled permanently.

This is matched-entry counterfactual analysis on the same baseline F5 trades;
earlier exits do not create new re-entry opportunities.
"""

PROTOCOL_VERSION = "STRATEGY_F5_PRETRAIL_RVI_FAILURE_EXIT_V1"
STRATEGY_ID = "F5"
ROLE = "MATCHED_ENTRY_PRETRAIL_SIGNAL_FAILURE_DIAGNOSTIC_NOT_VALIDATION"

RVI_FAILURE = {
    "indicator": "RELATIVE_VOLATILITY_INDEX",
    "length": 10,
    "centerline": 50.0,
    "consecutive_completed_2m_bars_below_centerline": 2,
    "active_only_before_trail_activation": True,
    "activation_precedence": True,
    "exit_price": "NEXT_2M_BAR_OPEN",
    "intrabar_trigger": False,
    "after_activation_baseline_f5_management_unchanged": True,
    "matched_entry_analysis": True,
    "no_extra_reentries_after_earlier_exit": True,
}

PRESERVATION_SCREEN = {
    "minimum_activated_trade_untouched_pct": 95.0,
    "minimum_baseline_winner_untouched_pct": 95.0,
    "require_net_pnl_improvement_each_month_zero_slippage": True,
    "require_pooled_net_pnl_improvement_at_0_5_slippage": True,
    "require_pooled_net_pnl_improvement_at_1_0_slippage": True,
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "keep_existing_post_activation_trail_frozen": True,
    "keep_macd_and_rvi_entry_frozen": True,
    "do_not_search_rvi_failure_thresholds_on_jul_sep": True,
    "do_not_search_consecutive_bar_count_on_jul_sep": True,
    "any_useful_result_requires_fresh_holdout": True,
    "strategy_d_remains_paused": True,
}
