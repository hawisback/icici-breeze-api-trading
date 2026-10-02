"""Frozen F5 option-premium support stop-loss development diagnostic.

Purpose
-------
Test whether structural support in the traded PE premium provides a better
pre-trail stop-loss boundary than NIFTY resistance or fixed percentage stops.

Development population
----------------------
Baseline F5 PE trades on Jul-Sep 2026 whole-day BEARISH NIFTY sessions. The
whole-day label selects the existing development population only. Every option
support level is fully known at entry.

Frozen support candidates
-------------------------
1. SIGNAL_BAR_LOW
   Low of the completed 2-minute option bar whose close generated the F5 entry
   decision. This is the most local structural invalidation level.

2. RECENT_10M_LOW
   Lowest low among completed 2-minute option bars in the ten minutes ending at
   the entry decision timestamp.

3. CONFIRMED_SWING_LOW_2X2
   Latest completed 2-minute option pivot low before entry, lower than the two
   preceding and two following completed 2-minute lows. Requiring the two
   following bars makes the pivot fully confirmed before entry.

Break rule
----------
A support break requires a completed 2-minute option CLOSE strictly below the
pre-entry support level. Exit at the next 2-minute option open, represented by
the decision timestamp at which that close becomes known.

Trail activation has precedence. If +10% trail activation occurs on or before
the support-break execution timestamp, the support stop cannot act. Likewise,
a baseline exit on or before that timestamp takes precedence.

No support buffers, ATR multipliers, pivot-width search, percentage offsets,
or confirmation-count search are allowed.
"""

from __future__ import annotations

PROTOCOL_VERSION = "STRATEGY_F5_OPTION_SUPPORT_STOP_V1"
STRATEGY_ID = "F5"
ROLE = "BEARISH_PE_OPTION_SUPPORT_STOP_DEVELOPMENT_DIAGNOSTIC"

DEVELOPMENT_WINDOW = {
    "start": "2026-07-01",
    "end": "2026-09-30",
    "months": ["2026-07", "2026-08", "2026-09"],
    "option_interval_minutes": 2,
}

SUPPORT_CANDIDATES = {
    "SIGNAL_BAR_LOW": {
        "kind": "LOW_OF_COMPLETED_2M_SIGNAL_BAR",
    },
    "RECENT_10M_LOW": {
        "kind": "LOWEST_COMPLETED_2M_LOW_IN_LOOKBACK",
        "lookback_minutes": 10,
    },
    "CONFIRMED_SWING_LOW_2X2": {
        "kind": "LATEST_CONFIRMED_2M_PIVOT_LOW",
        "left_bars": 2,
        "right_bars": 2,
    },
}

BREAK_RULE = {
    "confirmation": "FIRST_COMPLETED_2M_OPTION_CLOSE_STRICTLY_BELOW_SUPPORT",
    "execution": "NEXT_2M_OPTION_OPEN_AT_CONFIRMATION_DECISION_TIMESTAMP",
    "buffer_points": 0.0,
    "buffer_pct": 0.0,
    "required_consecutive_closes": 1,
    "trail_activation_precedence": True,
    "baseline_exit_precedence": True,
}

DEVELOPMENT_SCREEN = {
    "minimum_level_available_trades": 20,
    "minimum_breaks": 5,
    "minimum_target_winner_untouched_pct": 95.0,
    "minimum_target_activated_untouched_pct": 95.0,
    "require_pooled_net_pnl_improvement_zero_slippage": True,
    "require_pooled_net_pnl_improvement_half_point_slippage": True,
    "require_pooled_net_pnl_improvement_one_point_slippage": True,
    "require_monthly_net_pnl_improvement_each_month_zero_slippage": True,
    "purpose": "development_candidate_screen_only_not_validation",
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "whole_day_bearish_regime_only_selects_development_population": True,
    "support_levels_use_pre_entry_information_only": True,
    "breaks_use_completed_2m_option_bars_only": True,
    "target_only_bearish_regime_PE": True,
    "no_percentage_stop_search": True,
    "no_support_buffer_search": True,
    "no_pivot_width_search": True,
    "no_confirmation_count_search": True,
    "no_atr_multiplier_search": True,
    "trail_activation_precedence": True,
    "no_post_activation_change": True,
    "no_extra_reentry_after_counterfactual_exit": True,
    "existing_f5_entry_and_trail_frozen": True,
    "any_selected_candidate_requires_fresh_holdout": True,
    "strategy_d_remains_paused": True,
}
