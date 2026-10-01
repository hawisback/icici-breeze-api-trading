"""Corrected Strategy F4: option-native MACD + Relative Volatility Index(10).

This supersedes the earlier F4 Relative Vigor Index exploration, which used
the wrong indicator for the user's intended setup.

Indicator
---------
TradingView-style Relative Volatility Index (0..100):
- source: exact option contract close
- standard deviation length: 10
- internal up/down EMA smoothing length: 14
- stddev = population rolling stdev(close, 10)
- up input = stddev when close > close[1], otherwise 0
- down input = stddev when close <= close[1], otherwise 0
- up = EMA(up input, 14)
- down = EMA(down input, 14)
- RVI = 100 * up / (up + down)
- neutral fallback when denominator is zero: 50

Entry threshold exploration is predeclared at:
50, 55, 60, 65, 70, 75, 80.

MACD and execution remain unchanged from corrected Strategy F3:
- MACD(12,26,9) on exact option contract close
- bullish MACD crossover creates an entry opportunity
- held option exits on its own raw bearish MACD crossover
- fixed daily ATM strike from 09:15 NIFTY spot open
- next 5m bar open execution
- force flat 15:20
"""
from __future__ import annotations

PROTOCOL_VERSION = "STRATEGY_F4_MACD_RELATIVE_VOLATILITY_INDEX10_V1"
STRATEGY_ID = "F4"
STRATEGY_NAME = "OPTION_NATIVE_MACD_PLUS_RELATIVE_VOLATILITY_INDEX10"
ROLE = "EXPLORATORY_THRESHOLD_STABILITY_STUDY_NOT_VALIDATION"

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

RELATIVE_VOLATILITY_INDEX = {
    "name": "RELATIVE_VOLATILITY_INDEX",
    "abbreviation": "RVI",
    "scale": [0, 100],
    "source": "option_close",
    "stddev_length": 10,
    "directional_ema_length": 14,
    "stddev_ddof": 0,
    "flat_close_bucket": "DOWN",
    "zero_denominator_value": 50.0,
    "centerline": 50.0,
    "thresholds": [50, 55, 60, 65, 70, 75, 80],
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
    "one_position_across_ce_and_pe_per_threshold": True,
    "entry_signal": "BULLISH_OPTION_MACD_CROSS_AND_RVI_AT_OR_ABOVE_THRESHOLD",
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

ROBUSTNESS_SCREEN = {
    "minimum_trades_pooled": 30,
    "minimum_trades_each_month": 8,
    "require_positive_net_each_month": True,
    "require_profit_factor_above_one_each_month": True,
    "require_positive_net_at_0_5_slippage_each_month": True,
    "require_positive_pooled_net_at_1_0_slippage": True,
    "stable_threshold_region_requires_adjacent_threshold_pass": True,
    "purpose": "candidate_screen_only_not_validation",
}

SOURCE_COMPATIBILITY = {
    "accepted_raw_market_protocols": [
        "STRATEGY_F4_OPTION_NATIVE_MACD_RVI10_EXPLORATION_V1",
        "STRATEGY_F4_MACD_RELATIVE_VOLATILITY_INDEX10_V1",
    ],
    "note": (
        "The earlier market artifact contains raw spot/option OHLC only and can "
        "be reused. Its prior Relative Vigor interpretation is not reused."
    ),
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "previous_relative_vigor_results_invalid_for_this_indicator": True,
    "no_thresholds_beyond_50_to_80_in_5_point_steps": True,
    "no_macd_parameter_search": True,
    "no_rvi_length_search": True,
    "no_stop_target_search": True,
    "no_time_filter_search": True,
    "no_side_filter_search": True,
    "any_surviving_threshold_requires_new_holdout_month": True,
    "strategy_d_remains_paused": True,
}
