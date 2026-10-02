"""Frozen F5 broad entry-indicator atlas.

Purpose
-------
Use the existing Jul-Sep 2026 raw 1-minute option market artifact and the
existing F5 baseline trades to identify entry-time indicators associated with
bad trades, without changing entry/exit/trailing rules.

This is discovery only. No indicator threshold or combined rule is promoted
from this atlas. Any useful feature must be frozen afterward and validated on a
fresh month not used here.

Primary labels
--------------
BAD_TRADE:
    baseline F5 zero-slippage net PnL < 0 AND trail never activated.
TRAIL_ACTIVATED:
    baseline F5 trade later reached the frozen +10% activation level.
BASELINE_WINNER:
    baseline F5 zero-slippage net PnL > 0.

All features use only completed 2-minute bars available before the next-bar-open
entry.

Indicator families
------------------
Core:
- Relative Volatility Index(10)
- MACD(12,26,9), normalized histogram, histogram change
- entry premium

Momentum / oscillator:
- RSI(7), RSI(14)
- Stochastic %K(9), %K(14), %D(3)
- Williams %R(14)
- CCI(10), CCI(20)
- ROC(3), ROC(5), ROC(10)

Trend:
- price distance to EMA(9), EMA(20)
- EMA(9)-EMA(20) spread
- EMA(9) slope over 3 bars
- ADX(14), +DI(14), -DI(14), DI spread

Volatility / range:
- ATR%(5), ATR%(14)
- true-range expansion vs previous 10-bar median
- Bollinger %B(20,2)
- Bollinger bandwidth%(20,2)
- close-to-close realized volatility%(5), realized volatility%(20)
- short/long realized-volatility ratio

Volume / flow:
- volume ratio to prior 5-bar median
- volume z-score(20)
- OBV 5-bar change normalized by recent volume
- MFI(14)
- CMF(20)
- session VWAP distance%

Candle / microstructure:
- body% of close
- total range% of close
- close location value within bar
- upper wick% and lower wick%
- 1/2/3/5-bar returns

Structural context:
- minutes from session open
- days to expiry
- entry premium
- CE/PE reported separately; never used as a fitted side filter here

Evaluation
----------
For each continuous feature:
- bad-trade rank AUC (higher value predicts BAD)
- activation rank AUC (higher value predicts +10% activation)
- standardized mean difference
- medians/quartiles by label
- July/August/September AUC
- CE and PE AUC
- direction/sign consistency across months and sides
- missingness

No numeric cutpoint search, no feature-combination search, no model fitting.
"""

PROTOCOL_VERSION = "STRATEGY_F5_BROAD_ENTRY_INDICATOR_ATLAS_V1"
STRATEGY_ID = "F5"
ROLE = "BROAD_ENTRY_INDICATOR_DISCOVERY_NOT_VALIDATION"

CONTINUOUS_FEATURES = [
    "entry_premium",
    "rvi10",
    "macd_pct",
    "macd_hist_pct",
    "macd_hist_delta_pct",
    "rsi7",
    "rsi14",
    "stoch_k9",
    "stoch_k14",
    "stoch_d3",
    "williams_r14",
    "cci10",
    "cci20",
    "roc3_pct",
    "roc5_pct",
    "roc10_pct",
    "price_to_ema9_pct",
    "price_to_ema20_pct",
    "ema9_to_ema20_pct",
    "ema9_slope3_pct",
    "adx14",
    "plus_di14",
    "minus_di14",
    "di_spread14",
    "atr5_pct",
    "atr14_pct",
    "tr_expansion10",
    "bb_percent_b20",
    "bb_bandwidth20_pct",
    "realized_vol5_pct",
    "realized_vol20_pct",
    "realized_vol_ratio_5_20",
    "volume_ratio5",
    "volume_z20",
    "obv_change5_norm",
    "mfi14",
    "cmf20",
    "vwap_distance_pct",
    "body_pct",
    "range_pct",
    "close_location_value",
    "upper_wick_pct",
    "lower_wick_pct",
    "return1_pct",
    "return2_pct",
    "return3_pct",
    "return5_pct",
    "minutes_from_open",
    "days_to_expiry",
]

REPORTING = {
    "overall": True,
    "by_month": True,
    "by_side": True,
    "bad_trade_auc": True,
    "trail_activation_auc": True,
    "standardized_mean_difference": True,
    "distribution_quartiles": True,
    "direction_consistency": True,
    "missingness": True,
    "top_features_by_bad_trade_auc_distance": True,
    "top_features_by_activation_auc_distance": True,
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "keep_f5_entry_rule_unchanged": True,
    "keep_existing_post_activation_trail_frozen": True,
    "keep_existing_pre_activation_management_frozen": True,
    "no_numeric_cutpoint_search": True,
    "no_feature_combination_search": True,
    "no_side_filter_promotion": True,
    "no_time_filter_promotion": True,
    "no_stop_or_target_search": True,
    "fresh_holdout_required_after_feature_selection": True,
    "strategy_d_remains_paused": True,
}
