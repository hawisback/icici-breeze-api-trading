"""Frozen Strategy F4 exploration: option-native MACD plus RVI(10).

Purpose
-------
Evaluate whether TradingView-style Relative Vigor Index (RVI), length 10,
can improve the corrected option-native MACD strategy without changing the
MACD parameters, strike selection, exits, costs, or session handling.

Development months
------------------
2026-07-01 through 2026-09-30, analyzed month-by-month and pooled.
These months have already been inspected for raw MACD performance, so F4 is an
exploratory feature study, NOT validation.

Indicator definitions
---------------------
MACD: exact option contract close, EMA(12)-EMA(26), signal EMA(9).

TradingView-style Relative Vigor Index:
a = close-open
e = high-low
weighted numerator raw = (a + 2*a[1] + 2*a[2] + a[3]) / 6
weighted denominator raw = (e + 2*e[1] + 2*e[2] + e[3]) / 6
RVI = SMA(weighted numerator raw, 10) / SMA(weighted denominator raw, 10)
RVI signal = (RVI + 2*RVI[1] + 2*RVI[2] + RVI[3]) / 6

Exploration candidates
----------------------
BASELINE:
    raw bullish option-MACD crossover.

RVI_ABOVE_SIGNAL:
    baseline entry AND RVI > RVI signal.

RVI_ABOVE_ZERO:
    baseline entry AND RVI > 0.

RVI_ABOVE_SIGNAL_AND_ZERO:
    baseline entry AND RVI > RVI signal AND RVI > 0.

RVI_SPREAD_P50:
    baseline entry AND RVI > RVI signal AND (RVI-RVI signal) >= the pooled
    development median of positive RVI spreads at raw MACD entry opportunities.

RVI_SPREAD_P75:
    same, using the pooled development 75th percentile of positive RVI spreads.

RVI_LEVEL_P50:
    baseline entry AND RVI > 0 AND RVI >= pooled development median of positive
    RVI values at raw MACD entry opportunities.

RVI_LEVEL_P75:
    same, using the pooled development 75th percentile of positive RVI values.

Important: P50/P75 cut points are learned ONCE from pooled Jul-Sep entry
opportunities and then frozen for all per-month scoring. No per-month re-binning.

Exits remain the held option's raw bearish MACD crossover at next 5m open.
"""
from __future__ import annotations

PROTOCOL_VERSION = "STRATEGY_F4_OPTION_NATIVE_MACD_RVI10_EXPLORATION_V1"
STRATEGY_ID = "F4"
STRATEGY_NAME = "OPTION_NATIVE_MACD_PLUS_RVI10"
ROLE = "EXPLORATORY_FEATURE_STUDY_NOT_VALIDATION"

WINDOW = {
    "start": "2026-07-01",
    "end": "2026-09-30",
    "warmup_start": "2026-06-19",
    "warmup_previous_sessions_per_contract": 5,
    "provider": "BREEZE",
    "bar_interval": "5minute",
    "force_exit_time": "15:20",
    "months": ["2026-07", "2026-08", "2026-09"],
}

MACD = {
    "fast_length": 12,
    "slow_length": 26,
    "signal_length": 9,
    "source": "option_close",
    "ema_adjust": False,
    "minimum_prior_option_bars_before_session": 35,
}

RVI = {
    "indicator": "RELATIVE_VIGOR_INDEX",
    "length": 10,
    "source": "option_ohlc",
    "weighted_4bar_kernel": [1, 2, 2, 1],
    "kernel_divisor": 6.0,
    "signal_weighted_4bar_kernel": [1, 2, 2, 1],
    "signal_divisor": 6.0,
    "zero_denominator": "RVI_NAN",
}

OPTION_SELECTION = {
    "underlying": "NIFTY",
    "strike_step": 50,
    "daily_atm_reference": "09:15_NIFTY_SPOT_OPEN",
    "fixed_strike_for_entire_session": True,
    "rights": ["CE", "PE"],
    "expiry_policy": "NEAREST_NON_EXPIRED_WEEKLY_INCLUDING_0DTE",
    "weekly_expiries": [
        "2026-07-07",
        "2026-07-14",
        "2026-07-21",
        "2026-07-28",
        "2026-08-04",
        "2026-08-11",
        "2026-08-18",
        "2026-08-25",
        "2026-09-01",
        "2026-09-08",
        "2026-09-15",
        "2026-09-22",
        "2026-09-29",
        "2026-10-06",
    ],
    "lot_size": 65,
}

EXECUTION = {
    "one_position_across_ce_and_pe_per_candidate": True,
    "entry_price": "NEXT_5M_OPTION_BAR_OPEN",
    "exit_signal": "HELD_OPTION_RAW_BEARISH_MACD_CROSS",
    "exit_price": "NEXT_5M_OPTION_BAR_OPEN",
    "simultaneous_ce_pe_entry": "SKIP_AMBIGUOUS_TIMESTAMP",
    "same_timestamp_exit_then_new_entry_allowed": True,
    "force_exit_price": "15:20_OPTION_BAR_OPEN",
    "overnight_positions": False,
    "stop_loss": None,
    "profit_target": None,
}

COST_MODEL = {
    "primary_slippage_points_each_side": 0.0,
    "slippage_sensitivity_points_each_side": [0.0, 0.5, 1.0],
    "brokerage_per_order_inr": 20.0,
    "exchange_charge_rate": 0.0003503,
    "stt_sell_rate": 0.001,
    "gst_rate": 0.18,
    "sebi_charge_rate": 0.000001,
    "stamp_buy_rate": 0.00003,
    "version": "paper_options_costs_v1",
    "fidelity": "PARTIAL_FIDELITY_OHLC_OPEN_PLUS_EXPLICIT_COSTS",
}

CANDIDATES = [
    "BASELINE",
    "RVI_ABOVE_SIGNAL",
    "RVI_ABOVE_ZERO",
    "RVI_ABOVE_SIGNAL_AND_ZERO",
    "RVI_SPREAD_P50",
    "RVI_SPREAD_P75",
    "RVI_LEVEL_P50",
    "RVI_LEVEL_P75",
]

ROBUSTNESS_SCREEN = {
    "minimum_trades_pooled": 30,
    "minimum_trades_each_month": 8,
    "require_positive_net_each_month": True,
    "require_profit_factor_above_one_each_month": True,
    "require_positive_net_at_0_5_slippage_each_month": True,
    "require_positive_pooled_net_at_1_0_slippage": True,
    "purpose": "candidate_screen_only_not_validation",
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "development_months_are_inspected": True,
    "no_candidate_is_validated_by_this_study": True,
    "no_macd_parameter_search": True,
    "no_rvi_length_search": True,
    "no_stop_target_search": True,
    "no_time_filter_search": True,
    "no_side_filter_search": True,
    "no_thresholds_beyond_frozen_candidates": True,
    "any_surviving_candidate_requires_new_holdout_month": True,
    "strategy_d_remains_paused": True,
}
