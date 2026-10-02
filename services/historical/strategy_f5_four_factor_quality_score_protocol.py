"""Frozen F5 four-factor quality-score diagnostic.

Purpose
-------
Test whether the four conventional indicator states already established in
Jul-Sep research form a monotonic entry-quality scale WITHOUT filtering trades
or changing trade-path behavior.

Every original baseline F5 trade remains in the matched sample.

Score
-----
Add one point for each entry-time condition present on the completed 2-minute
signal bar:

1. ATR_ACTIVE:
   ATR(14)% >= median ATR(14)% of the PRIOR 20 completed 2-minute bars.
2. BB_ACTIVE:
   Bollinger Bandwidth(20,2)% >= median bandwidth of the PRIOR 20 completed
   2-minute bars.
3. STOCH_NOT_OVERBOUGHT:
   Stochastic %D(3) < 80.
4. HIST_STRONG:
   normalized MACD histogram >= 0.15% of option close.

QUALITY_SCORE ranges from 0 to 4.

Labels
------
BAD_TRADE:
    baseline F5 zero-slippage net PnL < 0 and trail never activated.
TRAIL_ACTIVATED:
    baseline F5 trade later reaches frozen +10% activation.
BASELINE_WINNER:
    baseline F5 zero-slippage net PnL > 0.

Evaluation
----------
For each score bucket 0..4, report:
- trade count
- bad-trade rate
- trail-activation rate
- baseline win rate
- zero-slippage net PnL
- average/median net PnL
- CE/PE split
- Jul/Aug/Sep split

Also report monotonicity checks across populated adjacent score buckets:
- bad-trade rate should non-increase as score rises
- activation rate should non-decrease as score rises
- win rate should non-decrease as score rises

No cutoff selection or trading-rule promotion is allowed in this pass.
"""

PROTOCOL_VERSION = "STRATEGY_F5_FOUR_FACTOR_QUALITY_SCORE_V1"
STRATEGY_ID = "F5"
ROLE = "MATCHED_ENTRY_QUALITY_SCORE_DIAGNOSTIC_NOT_VALIDATION"

SCORE_COMPONENTS = [
    "ATR_ACTIVE",
    "BB_ACTIVE",
    "STOCH_NOT_OVERBOUGHT",
    "HIST_STRONG",
]

SCORE_RANGE = [0, 1, 2, 3, 4]

MONOTONICITY = {
    "bad_trade_rate": "NON_INCREASING_WITH_SCORE",
    "trail_activation_rate": "NON_DECREASING_WITH_SCORE",
    "baseline_win_rate": "NON_DECREASING_WITH_SCORE",
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "matched_entry_only": True,
    "keep_all_original_f5_trades": True,
    "no_score_cutoff_selection": True,
    "no_score_weight_optimization": True,
    "no_component_threshold_retuning": True,
    "no_side_filter": True,
    "no_time_filter": True,
    "keep_existing_post_activation_trail_frozen": True,
    "any_future_score_rule_requires_fresh_holdout": True,
    "strategy_d_remains_paused": True,
}
