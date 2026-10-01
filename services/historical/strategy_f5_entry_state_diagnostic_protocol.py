"""Frozen F5 entry-state diagnostic.

Purpose
-------
Identify entry-time features that distinguish F5 trades which later reach the
existing +10% trail activation from trades which do not, WITHOUT changing the
entry rule, exit rule, or trail on the inspected Jul-Sep 2026 development data.

This is exploratory diagnosis, not validation and not a new strategy.

Primary outcome label
---------------------
TRAIL_ACTIVATED:
    The baseline F5 trade later reached the frozen +10% completed-close return
    and activated the existing trail.

Secondary outcome label
-----------------------
BASELINE_WINNER:
    Baseline F5 zero-slippage net PnL > 0.

Entry-state features
--------------------
All features are measured on the completed 2-minute SIGNAL BAR whose close
generated the next-bar-open entry. No future bars are used.

RVI:
- rvi_level: Relative Volatility Index(10)
- rvi_delta_1: RVI(t) - RVI(t-1)
- rvi_rising: rvi_delta_1 > 0

MACD:
- macd_hist_pct: 100 * (MACD - signal) / option_close
- macd_hist_delta_pct: 100 * ((hist_t - hist_t-1) / option_close)
- macd_hist_expanding: macd_hist_delta_pct > 0
- macd_above_zero: MACD > 0
- macd_pct: 100 * MACD / option_close

Price momentum:
- return_1bar_pct: close_t / close_t-1 - 1
- return_2bar_pct: close_t / close_t-2 - 1
- return_3bar_pct: close_t / close_t-3 - 1

Volume:
- volume_ratio_5: current 2m volume / median(previous 5 completed 2m volumes)
- volume_expanding: volume_ratio_5 > 1
Missing/zero prior volume produces null, not an imputed signal.

Frozen natural-state diagnostics
--------------------------------
These are descriptive states, not promoted filters:
- RVI_RISING
- MACD_HIST_EXPANDING
- MACD_ABOVE_ZERO
- MOMENTUM_3BAR_POSITIVE
- VOLUME_EXPANDING
- RVI_RISING_AND_HIST_EXPANDING
- RVI_RISING_AND_MOMENTUM_3BAR_POSITIVE
- HIST_EXPANDING_AND_MOMENTUM_3BAR_POSITIVE

No numeric cut-point search is allowed in this study. Continuous-feature
separation is reported with medians, quartiles, standardized mean difference,
and rank AUC for TRAIL_ACTIVATED vs NOT_ACTIVATED.
"""

PROTOCOL_VERSION = "STRATEGY_F5_ENTRY_STATE_DIAGNOSTIC_V1"
STRATEGY_ID = "F5"
ROLE = "ENTRY_STATE_DIAGNOSTIC_NOT_VALIDATION"

PRIMARY_LABEL = "TRAIL_ACTIVATED"
SECONDARY_LABEL = "BASELINE_WINNER"

CONTINUOUS_FEATURES = [
    "rvi_level",
    "rvi_delta_1",
    "macd_hist_pct",
    "macd_hist_delta_pct",
    "macd_pct",
    "return_1bar_pct",
    "return_2bar_pct",
    "return_3bar_pct",
    "volume_ratio_5",
]

NATURAL_STATES = [
    "RVI_RISING",
    "MACD_HIST_EXPANDING",
    "MACD_ABOVE_ZERO",
    "MOMENTUM_3BAR_POSITIVE",
    "VOLUME_EXPANDING",
    "RVI_RISING_AND_HIST_EXPANDING",
    "RVI_RISING_AND_MOMENTUM_3BAR_POSITIVE",
    "HIST_EXPANDING_AND_MOMENTUM_3BAR_POSITIVE",
]

REPORTING = {
    "overall_feature_separation": True,
    "monthwise_feature_separation": True,
    "ce_pe_feature_separation": True,
    "natural_state_activation_rate": True,
    "natural_state_baseline_pnl": True,
    "natural_state_trade_count": True,
    "missingness": True,
    "rank_auc": True,
    "standardized_mean_difference": True,
    "no_threshold_optimization": True,
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "keep_existing_entry_rule_frozen": True,
    "keep_existing_post_activation_trail_frozen": True,
    "keep_existing_pre_activation_management_frozen": True,
    "no_numeric_cutpoint_search": True,
    "no_feature_combination_search_beyond_frozen_natural_states": True,
    "no_stop_or_target_search": True,
    "no_time_filter_search": True,
    "no_side_filter_search": True,
    "any_new_entry_rule_must_be_frozen_before_fresh_holdout": True,
    "strategy_d_remains_paused": True,
}
