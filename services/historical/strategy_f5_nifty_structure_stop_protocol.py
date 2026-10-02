"""Frozen F5 NIFTY-structure stop-loss development diagnostic.

Purpose
-------
Identify whether a bearish-PE trade should be exited when the underlying NIFTY
invalidates pre-entry resistance. This is a market-structure stop study, not a
percentage-stop search.

Development population
----------------------
Baseline F5 PE trades on Jul-Sep 2026 whole-day BEARISH NIFTY sessions. The
whole-day label is used only to select the already-established development
population; every resistance level and breach decision uses information that
was available at the relevant timestamp.

Frozen resistance candidates
----------------------------
1. CONFIRMED_SWING_HIGH_2X2
   Latest completed 5-minute NIFTY pivot high before entry. A pivot high is
   higher than the two preceding and two following completed 5-minute highs.
   Requiring both following bars means the pivot is confirmed before entry.

2. RECENT_30M_HIGH
   Highest NIFTY high among completed 5-minute bars whose starts fall within
   the 30 minutes before entry.

3. SESSION_HIGH_TO_ENTRY
   Highest NIFTY high among all completed 5-minute bars from 09:15 through the
   information set available at entry.

Stop trigger
------------
For a PE trade, the frozen resistance is breached only when a completed
5-minute NIFTY bar CLOSES strictly above the pre-entry resistance level.
Execution is the first available 2-minute option open at or after that 5-minute
bar's completion time.

Trail activation has precedence. If the baseline +10% trail activates on or
before the counterfactual structure-stop execution timestamp, the structure
stop cannot act. Likewise, if the baseline trade has already exited, the
structure stop cannot act.

No resistance buffers, ATR multipliers, pivot-width searches, close-count
confirmation searches, or percentage-stop combinations are allowed here.
"""

from __future__ import annotations

PROTOCOL_VERSION = "STRATEGY_F5_NIFTY_STRUCTURE_STOP_V1"
STRATEGY_ID = "F5"
ROLE = "BEARISH_PE_NIFTY_RESISTANCE_STOP_DEVELOPMENT_DIAGNOSTIC"

DEVELOPMENT_WINDOW = {
    "start": "2026-07-01",
    "end": "2026-09-30",
    "months": ["2026-07", "2026-08", "2026-09"],
    "spot_interval_minutes": 5,
    "session_start": "09:15",
}

RESISTANCE_CANDIDATES = {
    "CONFIRMED_SWING_HIGH_2X2": {
        "kind": "LATEST_CONFIRMED_5M_PIVOT_HIGH",
        "left_bars": 2,
        "right_bars": 2,
    },
    "RECENT_30M_HIGH": {
        "kind": "HIGHEST_COMPLETED_5M_HIGH_IN_LOOKBACK",
        "lookback_minutes": 30,
    },
    "SESSION_HIGH_TO_ENTRY": {
        "kind": "HIGHEST_COMPLETED_5M_HIGH_FROM_SESSION_OPEN",
    },
}

BREACH_RULE = {
    "confirmation": "FIRST_COMPLETED_5M_NIFTY_CLOSE_STRICTLY_ABOVE_LEVEL",
    "execution": "FIRST_AVAILABLE_2M_OPTION_OPEN_AT_OR_AFTER_CONFIRMATION",
    "buffer_points": 0.0,
    "buffer_pct": 0.0,
    "required_consecutive_closes": 1,
    "trail_activation_precedence": True,
    "baseline_exit_precedence": True,
}

DEVELOPMENT_SCREEN = {
    "minimum_level_available_trades": 20,
    "minimum_breaches": 5,
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
    "resistance_levels_use_pre_entry_information_only": True,
    "breaches_use_completed_5m_bars_only": True,
    "target_only_bearish_regime_PE": True,
    "no_percentage_stop_search": True,
    "no_resistance_buffer_search": True,
    "no_pivot_width_search": True,
    "no_confirmation_count_search": True,
    "no_atr_multiplier_search": True,
    "trail_activation_precedence": True,
    "no_post_activation_change": True,
    "existing_f5_entry_and_trail_frozen": True,
    "any_selected_candidate_requires_fresh_holdout": True,
    "strategy_d_remains_paused": True,
}
