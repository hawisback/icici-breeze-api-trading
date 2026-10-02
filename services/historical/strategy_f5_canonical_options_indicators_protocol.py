"""Frozen F5 canonical options-indicator diagnostic.

Focus only on indicators/metrics commonly used in practical options trading.

Already established chart-side indicators retained:
- MACD histogram strength
- ATR(14)% of option premium
- Bollinger bandwidth(20,2)
- ADX(14)
- RSI(14)
- Stochastic %D(3)
- session VWAP distance

Options-specific metrics added from existing Breeze data:
- open interest level normalized to prior 20-bar median
- OI change over 1, 3 and 5 completed 2-minute bars
- OI z-score over prior 20 bars
- volume / open-interest ratio
- volume / prior 5-bar median
- price+OI state:
  LONG_BUILDUP      price up, OI up
  SHORT_COVERING    price up, OI down
  SHORT_BUILDUP     price down, OI up
  LONG_UNWINDING    price down, OI down
- days to expiry
- entry premium

No IV/Greeks are included because the current stored artifact does not contain
them. They must be added only from a documented source or a separately frozen
pricing-model protocol.

Primary label:
BAD_TRADE = zero-slippage net PnL < 0 and trail never activated.

Secondary:
TRAIL_ACTIVATED = later reaches frozen +10% trail activation.

This is diagnosis only: no threshold grid, no combined-rule search, no side
filter promotion, and no changes to the F5 trail.
"""

PROTOCOL_VERSION = "STRATEGY_F5_CANONICAL_OPTIONS_INDICATORS_V1"
STRATEGY_ID = "F5"
ROLE = "CANONICAL_OPTIONS_INDICATOR_DIAGNOSTIC_NOT_VALIDATION"

CONTINUOUS_FEATURES = [
    "entry_premium",
    "days_to_expiry",
    "macd_hist_pct",
    "atr14_pct",
    "bb_bandwidth20_pct",
    "adx14",
    "rsi14",
    "stoch_d3",
    "vwap_distance_pct",
    "oi_to_prior20_median",
    "oi_change1_pct",
    "oi_change3_pct",
    "oi_change5_pct",
    "oi_z20",
    "volume_to_oi",
    "volume_ratio5",
]

OI_STATES = [
    "LONG_BUILDUP",
    "SHORT_COVERING",
    "SHORT_BUILDUP",
    "LONG_UNWINDING",
    "FLAT_OR_MISSING",
]

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "no_iv_or_greeks_without_explicit_data_or_model_protocol": True,
    "no_numeric_cutpoint_search": True,
    "no_feature_combination_search": True,
    "no_side_filter_promotion": True,
    "keep_existing_post_activation_trail_frozen": True,
    "fresh_holdout_required_after_feature_selection": True,
    "strategy_d_remains_paused": True,
}
