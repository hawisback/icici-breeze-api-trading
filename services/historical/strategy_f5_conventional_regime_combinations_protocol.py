"""Frozen F5 conventional indicator-regime combination screen.

Development data: Jul-Sep 2026 only.

Goal
----
Test a very small, predeclared set of familiar indicator confirmations while
keeping the complete F5 entry/exit/trailing semantics unchanged.

Natural regime definitions
--------------------------
ATR_ACTIVE:
    current ATR(14)% >= median ATR(14)% of the PRIOR 20 completed 2-minute bars.

BB_ACTIVE:
    current Bollinger Bandwidth(20,2)% >= median bandwidth of the PRIOR 20
    completed 2-minute bars.

STOCH_NOT_OVERBOUGHT:
    Stochastic %D(3) < 80, the standard overbought boundary.

HIST_STRONG:
    normalized MACD histogram >= 0.15% of option close. This threshold was
    already frozen previously and is not re-tuned here.

Candidates
----------
1. ATR_ACTIVE
2. BB_ACTIVE
3. ATR_BB_ACTIVE
4. ATR_BB_STOCH80
5. ATR_BB_HIST015
6. ATR_BB_STOCH80_HIST015

No numeric threshold grid is allowed. If multiple candidates pass the frozen
screen, select the first in CANDIDATE_PRIORITY: simplicity/evidence order, not
best in-sample PnL.

Any selected candidate remains development-derived and requires a fresh
holdout. The existing +10% trail activation and trailing logic remain frozen.
"""

PROTOCOL_VERSION = "STRATEGY_F5_CONVENTIONAL_REGIME_COMBINATIONS_V1"
STRATEGY_ID = "F5"
ROLE = "SMALL_PREDECLARED_DEVELOPMENT_SCREEN_NOT_VALIDATION"

REGIME = {
    "atr_length": 14,
    "atr_active_reference": "PRIOR_20_BAR_MEDIAN_EXCLUDING_CURRENT",
    "bb_length": 20,
    "bb_stddev": 2.0,
    "bb_active_reference": "PRIOR_20_BAR_MEDIAN_EXCLUDING_CURRENT",
    "stochastic_k_length": 14,
    "stochastic_d_length": 3,
    "stochastic_not_overbought_max_exclusive": 80.0,
    "histogram_strength_pct_min": 0.15,
}

CANDIDATES = {
    "ATR_ACTIVE": ["ATR_ACTIVE"],
    "BB_ACTIVE": ["BB_ACTIVE"],
    "ATR_BB_ACTIVE": ["ATR_ACTIVE", "BB_ACTIVE"],
    "ATR_BB_STOCH80": [
        "ATR_ACTIVE",
        "BB_ACTIVE",
        "STOCH_NOT_OVERBOUGHT",
    ],
    "ATR_BB_HIST015": [
        "ATR_ACTIVE",
        "BB_ACTIVE",
        "HIST_STRONG",
    ],
    "ATR_BB_STOCH80_HIST015": [
        "ATR_ACTIVE",
        "BB_ACTIVE",
        "STOCH_NOT_OVERBOUGHT",
        "HIST_STRONG",
    ],
}

CANDIDATE_PRIORITY = [
    "ATR_ACTIVE",
    "BB_ACTIVE",
    "ATR_BB_ACTIVE",
    "ATR_BB_STOCH80",
    "ATR_BB_HIST015",
    "ATR_BB_STOCH80_HIST015",
]

DEVELOPMENT_SCREEN = {
    "minimum_trades": 60,
    "minimum_trades_each_month": 15,
    "minimum_trades_each_side": 20,
    "minimum_baseline_activated_signal_capture_pct": 65.0,
    "minimum_baseline_winner_signal_capture_pct": 65.0,
    "require_pooled_activation_rate_above_baseline": True,
    "minimum_months_activation_rate_above_baseline": 2,
    "require_pooled_net_above_baseline_all_slippages": True,
    "minimum_months_zero_slippage_net_above_baseline": 2,
    "require_zero_slippage_max_drawdown_below_baseline": True,
    "selection": "FIRST_PASSING_CANDIDATE_IN_PRIORITY_ORDER",
    "purpose": "development_candidate_selection_only",
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "keep_rvi10_ge_50_base_entry": True,
    "keep_existing_pre_activation_management_frozen": True,
    "keep_existing_post_activation_trail_frozen": True,
    "no_numeric_threshold_search": True,
    "no_candidate_combinations_beyond_frozen_set": True,
    "no_side_filter": True,
    "no_time_filter": True,
    "no_stop_or_target_search": True,
    "selected_candidate_requires_fresh_holdout": True,
    "strategy_d_remains_paused": True,
}
