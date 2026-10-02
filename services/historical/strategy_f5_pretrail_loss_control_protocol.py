"""Frozen Strategy F5 pre-trail loss-control diagnostic.

Objective
---------
Reduce losses on F5 setups that fail BEFORE the existing +10% trail activation,
without changing management of trades that become the desired short swings.

The baseline Strategy F5 is frozen:
- 2-minute option MACD(12,26,9)
- Relative Volatility Index(10) >= 50 at bullish MACD crossover
- +10% completed-close return activates the existing trail
- after activation, the existing close-confirmed 10-point trail is unchanged

This study changes ONLY the pre-activation loss-control rule.

Protective-stop candidates
--------------------------
Completed 2-minute close return from entry <=:
-5%, -7.5%, -10%, -12.5%, -15%, -20%

If breached before trail activation:
- exit at the next 2-minute open
- intrabar lows are ignored
- if a trade reaches +10% on a completed close, trail activation takes priority;
  the protective stop can never act after activation

Evaluation is MATCHED-ENTRY counterfactual analysis using the exact baseline
F5 trailing trades. Earlier exits do not create extra re-entry opportunities.
This isolates whether the stop improves loss control without changing the entry
set.

These Jul-Sep months are development data. Any useful stop must be validated on
a fresh holdout period before promotion.
"""
from __future__ import annotations

PROTOCOL_VERSION = "STRATEGY_F5_PRETRAIL_LOSS_CONTROL_V1"
STRATEGY_ID = "F5"
ROLE = "MATCHED_ENTRY_PRETRAIL_STOP_DIAGNOSTIC_NOT_VALIDATION"

STOP_CANDIDATES_PCT = [5.0, 7.5, 10.0, 12.5, 15.0, 20.0]

STOP_EXECUTION = {
    "active_only_before_trail_activation": True,
    "breach_reference": "COMPLETED_2M_CLOSE_RETURN_FROM_ENTRY",
    "breach_operator": "CLOSE_RETURN_AT_OR_BELOW_NEGATIVE_STOP_PCT",
    "exit_price": "NEXT_2M_BAR_OPEN",
    "intrabar_low_touch_exit": False,
    "trail_activation_precedence": True,
    "after_activation_baseline_f5_management_unchanged": True,
    "matched_entry_analysis": True,
    "no_extra_reentries_after_earlier_stop": True,
}

PRESERVATION_SCREEN = {
    "minimum_activated_trade_untouched_pct": 95.0,
    "minimum_baseline_winner_untouched_pct": 95.0,
    "require_net_pnl_improvement_each_month_zero_slippage": True,
    "require_pooled_net_pnl_improvement_at_0_5_slippage": True,
    "require_pooled_net_pnl_improvement_at_1_0_slippage": True,
    "purpose": "development_candidate_screen_only_not_validation",
}

REPORTING = {
    "preactivation_mae_distribution": True,
    "activated_vs_nonactivated_mae": True,
    "baseline_winner_preservation": True,
    "activated_trade_preservation": True,
    "stops_triggered": True,
    "monthly_net_pnl": True,
    "zero_half_one_point_slippage": True,
    "max_drawdown": True,
    "largest_loss": True,
    "net_delta_vs_baseline": True,
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "do_not_change_trail_activation": True,
    "do_not_change_trail_distance": True,
    "do_not_change_macd": True,
    "do_not_change_rvi": True,
    "do_not_add_time_filter": True,
    "do_not_add_side_filter": True,
    "no_stop_thresholds_beyond_frozen_ladder": True,
    "any_selected_stop_requires_fresh_holdout": True,
    "strategy_d_remains_paused": True,
}
