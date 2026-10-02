"""Frozen Strategy F5: 2-minute option MACD + Relative Volatility Index(10)
with a close-confirmed profit trail.

This protocol is based on the user's annotated option-chart example. It is a
new strategy definition and does not reuse the earlier 5-minute RVI threshold
search as an entry threshold optimization.

Signal chart
------------
- exact NIFTY option contract, 2-minute OHLC aggregated from Breeze 1-minute data
- MACD(12,26,9) on option close
- Relative Volatility Index length 10 on option close
- entry requires bullish MACD crossover AND Relative Volatility Index >= 50

Trade management
----------------
- entry at next 2-minute bar open
- before trail activation, held option bearish MACD crossover exits at next 2m open
- once a completed 2-minute close reaches +10% gross premium return, trail activates
- on the activation bar, trail activation takes precedence over a bearish MACD cross
- after activation, bearish MACD crosses are ignored; the trail manages the swing
- trail is 10 percentage points of INITIAL deployed trade capital behind the best
  completed 2-minute CLOSE return
- initial activated floor is therefore 0% when peak close return is +10%
- floor ratchets continuously upward and never loosens
- a trail breach requires a COMPLETED 2-minute CLOSE at/below the previously active
  floor; exit is at the following 2-minute bar OPEN
- intrabar LOW touches do not trigger the trail
- new floor calculated from a completed bar is actionable only from the next bar
- force flat at the raw 1-minute 15:20 open
- no overnight positions

A raw-exit comparison using the exact same entries is reported to isolate the
effect of the trailing management.
"""
from __future__ import annotations

PROTOCOL_VERSION = "STRATEGY_F5_2MIN_MACD_RVI10_TRAIL10_V1"
STRATEGY_ID = "F5"
STRATEGY_NAME = "OPTION_2MIN_MACD_RVI10_TRAIL10"
ROLE = "EXPLORATORY_DEVELOPMENT_NOT_VALIDATION"

WINDOW = {
    "start": "2026-07-01",
    "end": "2026-09-30",
    "warmup_start": "2026-06-19",
    "warmup_previous_sessions_per_contract": 5,
    "months": ["2026-07", "2026-08", "2026-09"],
    "provider": "BREEZE",
    "source_interval": "1minute",
    "signal_interval_minutes": 2,
    "session_start": "09:15",
    "session_end_exclusive": "15:30",
    "last_entry_time": "15:15",
    "force_exit_time": "15:20",
}

MACD = {
    "fast_length": 12,
    "slow_length": 26,
    "signal_length": 9,
    "source": "option_close",
    "ema_adjust": False,
    "minimum_prior_2m_bars_before_session": 35,
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
    "entry_threshold": 50.0,
}

OPTION_SELECTION = {
    "underlying": "NIFTY",
    "strike_step": 50,
    "daily_atm_reference": "09:15_NIFTY_SPOT_OPEN",
    "fixed_strike_for_entire_session": True,
    "rights": ["CE", "PE"],
    "expiry_policy": "NEAREST_NON_EXPIRED_WEEKLY_INCLUDING_0DTE",
    "weekly_expiries": [
        "2026-07-07", "2026-07-14", "2026-07-21", "2026-07-28",
        "2026-08-04", "2026-08-11", "2026-08-18", "2026-08-25",
        "2026-09-01", "2026-09-08", "2026-09-15", "2026-09-22",
        "2026-09-29", "2026-10-06",
    ],
    "lot_size": 65,
}

TRAIL = {
    "activation_return_pct": 10.0,
    "distance_pct_of_initial_trade_capital": 10.0,
    "peak_reference": "BEST_COMPLETED_2M_CLOSE_RETURN",
    "breach_reference": "COMPLETED_2M_CLOSE",
    "breach_operator": "CLOSE_AT_OR_BELOW_ACTIVE_FLOOR",
    "ratchet": True,
    "floor_never_decreases": True,
    "new_floor_effective_from_next_bar": True,
    "intrabar_low_touch_exit": False,
    "activation_precedes_same_bar_bearish_macd_exit": True,
    "after_activation_ignore_bearish_macd": True,
    "return_basis": "RAW_OPTION_PREMIUM_VS_ENTRY_OPEN",
}

CANDIDATES = {
    "RAW_MACD_EXIT": {
        "entry": "BULLISH_MACD_CROSS_AND_RVI_GE_50",
        "exit": "BEARISH_MACD_CROSS_OR_FORCE_EXIT",
    },
    "TRAIL10_CLOSE_CONFIRMED": {
        "entry": "BULLISH_MACD_CROSS_AND_RVI_GE_50",
        "pre_activation_exit": "BEARISH_MACD_CROSS_OR_FORCE_EXIT",
        "post_activation_exit": "TRAIL10_CLOSE_CONFIRMED_OR_FORCE_EXIT",
    },
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
    "fidelity": "2MIN_OHLC_FROM_1MIN_PLUS_CLOSE_CONFIRMED_TRAIL_AND_EXPLICIT_COSTS",
}

REPORTING = {
    "compare_raw_exit_vs_trailing": True,
    "overall_and_monthly": True,
    "ce_pe_split": True,
    "trail_activation_rate": True,
    "trail_exit_rate": True,
    "average_peak_return_pct": True,
    "average_locked_floor_pct": True,
    "profit_giveback_from_peak_pct": True,
    "hold_minutes": True,
    "slippage_sensitivity": True,
    "coverage_and_skips": True,
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "rvi_threshold_fixed_at_50": True,
    "rvi_length_fixed_at_10": True,
    "trail_activation_fixed_at_10pct": True,
    "trail_distance_fixed_at_10pct": True,
    "no_trail_width_search_on_jul_sep": True,
    "no_macd_parameter_search": True,
    "no_stop_target_search": True,
    "no_time_filter_search": True,
    "no_side_filter_search": True,
    "any_positive_result_requires_fresh_holdout": True,
    "strategy_d_remains_paused": True,
}
