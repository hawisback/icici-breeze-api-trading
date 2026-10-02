"""Frozen August 2026 replication for corrected Strategy F3 option-native MACD.

The September 2026 development run was inspected before this protocol was
frozen. Therefore:
- the full symmetric F3 strategy is replicated unchanged;
- the September PE-side asymmetry is a predeclared secondary replication
  question only, not an execution filter;
- no MACD parameters, time windows, stops, targets, strike rules, or side rules
  may be changed before August is scored.
"""
from __future__ import annotations

PROTOCOL_VERSION = "STRATEGY_F3_AUGUST_REPLICATION_V1"
STRATEGY_ID = "F3"
STRATEGY_NAME = "OPTION_NATIVE_MACD"
ROLE = "SEPARATE_MONTH_HISTORICAL_REPLICATION"

BACKTEST_WINDOW = {
    "start": "2026-08-01",
    "end": "2026-08-31",
    "option_warmup_start": "2026-07-20",
    "option_warmup_previous_sessions_per_contract": 5,
    "provider": "BREEZE",
    "bar_interval": "5minute",
    "force_exit_time": "15:20",
}

MACD = {
    "fast_length": 12,
    "slow_length": 26,
    "signal_length": 9,
    "source": "option_close",
    "ema_adjust": False,
    "continuous_per_exact_contract_across_sessions": True,
    "minimum_prior_option_bars_before_session": 35,
}

OPTION_SELECTION = {
    "underlying": "NIFTY",
    "strike_step": 50,
    "daily_atm_reference": "09:15_NIFTY_SPOT_OPEN",
    "fixed_strike_for_entire_session": True,
    "rights": ["CE", "PE"],
    "expiry_policy": "NEAREST_NON_EXPIRED_WEEKLY_INCLUDING_0DTE",
    "weekly_expiries": [
        "2026-08-04",
        "2026-08-11",
        "2026-08-18",
        "2026-08-25",
        "2026-09-01",
    ],
    "lot_size": 65,
}

SIGNALS = {
    "entry": "OPTION_MACD_PREV <= OPTION_SIGNAL_PREV AND OPTION_MACD_NOW > OPTION_SIGNAL_NOW",
    "exit": "HELD_OPTION_MACD_PREV >= HELD_OPTION_SIGNAL_PREV AND HELD_OPTION_MACD_NOW < HELD_OPTION_SIGNAL_NOW",
    "signal_known_at": "completed_option_5m_bar_end",
}

EXECUTION = {
    "one_position_across_ce_and_pe": True,
    "entry_price": "NEXT_5M_OPTION_BAR_OPEN",
    "exit_price": "NEXT_5M_OPTION_BAR_OPEN",
    "simultaneous_ce_pe_bullish_entry": "SKIP_AMBIGUOUS_TIMESTAMP",
    "same_timestamp_exit_then_opposite_entry_allowed": True,
    "force_exit_price": "15:20_OPTION_BAR_OPEN",
    "allow_entry_at_or_after_force_exit": False,
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
    "bid_ask_available": False,
    "fidelity": "PARTIAL_FIDELITY_OHLC_OPEN_PLUS_EXPLICIT_COSTS",
}

REPLICATION_QUESTIONS = {
    "full_strategy": (
        "Does unchanged option-native F3 retain positive after-cost performance "
        "in August, including under modest slippage?"
    ),
    "pe_asymmetry_secondary": (
        "Does the separately reported PE leg remain positive with profit factor "
        "above 1 under the same frozen execution and slippage assumptions?"
    ),
    "ce_pe_reporting_only_not_execution_filter": True,
}

REPORTING = {
    "overall": True,
    "ce_and_pe_separately": True,
    "daily_pnl": True,
    "time_of_day": True,
    "win_rate": True,
    "profit_factor": True,
    "max_drawdown": True,
    "largest_winner_concentration": True,
    "slippage_sensitivity": True,
    "coverage_and_skips": True,
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "no_dynamic_atm_splicing": True,
    "no_pe_only_execution_filter": True,
    "no_macd_parameter_optimization": True,
    "no_extra_filters": True,
    "no_time_of_day_filter": True,
    "no_stop_target_search": True,
    "strategy_d_remains_paused": True,
}
