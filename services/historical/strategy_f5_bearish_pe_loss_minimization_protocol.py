"""Frozen F5 bearish-PE loss-minimization development diagnostic.

Purpose
-------
The dominant-BEARISH NIFTY + PE setup is the strongest positive F5 context in
Jul-Sep 2026. This study asks whether losses inside that already-good setup can
be reduced without damaging the trades that later activate the existing +10%
trail.

Prior F5 loss-control work already rejected:
- fixed pre-activation percentage stops, and
- two consecutive RVI(10)<50 bars.
Those rules cut too many eventual winners/activated trades.

This study therefore tests a different concept: LACK OF FOLLOW-THROUGH after
entry. It does not change the F5 entry signal and never acts after trail
activation.

Frozen candidate family
-----------------------
1. NO_POSITIVE_CLOSE_BY_6M
   At 6 minutes after entry, if no completed 2-minute close since entry has
   closed above the entry price, exit at that decision timestamp's 2-minute
   open.

2. NO_POSITIVE_CLOSE_BY_10M
   Same rule at 10 minutes.

3. MFE_LT_2PCT_BY_10M
   At 10 minutes, if the best completed-close return since entry is still below
   +2%, exit at that decision timestamp's 2-minute open.

For every candidate:
- only baseline F5 trades on whole-day BEARISH NIFTY regime + PE are evaluated,
- if the frozen +10% trail has activated on or before the checkpoint, the trade
  is untouched,
- if baseline F5 already exited on or before the checkpoint, it is untouched,
- no re-entry is created after an earlier counterfactual exit,
- post-activation trail management remains exactly frozen.

Breadth-confirmed bearish PE is reported as a prespecified secondary slice.
Breadth UNAVAILABLE is never folded into disagreement.

This is Jul-Sep development research only. Even a passing candidate requires a
fresh holdout before any rule promotion.
"""
from __future__ import annotations

PROTOCOL_VERSION = "STRATEGY_F5_BEARISH_PE_LOSS_MINIMIZATION_V1"
STRATEGY_ID = "F5"
ROLE = "BEARISH_PE_LACK_OF_PROGRESS_DEVELOPMENT_DIAGNOSTIC_NOT_VALIDATION"

CANDIDATES = {
    "NO_POSITIVE_CLOSE_BY_6M": {
        "checkpoint_minutes": 6,
        "condition": "MAX_COMPLETED_CLOSE_RETURN_PCT_LE_0",
    },
    "NO_POSITIVE_CLOSE_BY_10M": {
        "checkpoint_minutes": 10,
        "condition": "MAX_COMPLETED_CLOSE_RETURN_PCT_LE_0",
    },
    "MFE_LT_2PCT_BY_10M": {
        "checkpoint_minutes": 10,
        "condition": "MAX_COMPLETED_CLOSE_RETURN_PCT_LT_2",
    },
}

PRESERVATION_SCREEN = {
    "minimum_target_winner_untouched_pct": 95.0,
    "minimum_target_activated_untouched_pct": 95.0,
    "require_pooled_net_pnl_improvement_zero_slippage": True,
    "require_monthly_net_pnl_improvement_each_month_zero_slippage": True,
    "require_pooled_net_pnl_improvement_half_point_slippage": True,
    "require_pooled_net_pnl_improvement_one_point_slippage": True,
    "purpose": "development_candidate_screen_only_not_validation",
}

ANATOMY_CHECKPOINTS_MINUTES = [6, 10]

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "whole_day_bearish_regime_is_descriptive_development_context": True,
    "target_only_bearish_regime_PE": True,
    "breadth_confirmed_is_secondary_slice_not_required_gate": True,
    "missing_breadth_separate_from_disagreement": True,
    "no_entry_rule_change": True,
    "no_post_activation_change": True,
    "trail_activation_precedence": True,
    "no_extra_reentry_after_counterfactual_exit": True,
    "no_more_fixed_stop_search": True,
    "no_more_rvi_failure_exit_search": True,
    "no_candidate_thresholds_beyond_frozen_family": True,
    "any_selected_candidate_requires_fresh_holdout": True,
    "strategy_d_remains_paused": True,
}
