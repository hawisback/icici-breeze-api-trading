"""Frozen Strategy F3: option-native MACD on NIFTY CE/PE premium charts.

This corrects the earlier Strategy F/F2 interpretation. MACD is NOT calculated
on NIFTY spot and then mapped to an option. Instead, each option contract's own
5-minute premium close drives its own MACD signal.

Development window:
- 2026-09-01 through 2026-09-30 (the user's requested last complete month)
- option MACD warmup collection begins 2026-08-20

Daily contract selection:
- use NIFTY spot 09:15 bar OPEN to choose a fixed ATM strike for that session
- nearest non-expired weekly expiry, including 0DTE
- select both CE and PE at that fixed strike
- do not dynamically change strike intraday and do not splice contracts

Trading:
- compute MACD(12,26,9) separately on each exact option contract close series
- bullish option-MACD crossover => long that option at next 5m option-bar OPEN
- bearish crossover on the held option => exit at next 5m option-bar OPEN
- one option position at a time across CE and PE
- if CE and PE generate simultaneous bullish entries while flat, skip that
  timestamp as ambiguous rather than imposing discretionary tie-breaking
- force flat at 15:20
- no stop, target, zero-line filter, histogram filter, or parameter tuning
"""
from __future__ import annotations

PROTOCOL_VERSION = "STRATEGY_F3_OPTION_NATIVE_MACD_V1"
STRATEGY_ID = "F3"
STRATEGY_NAME = "OPTION_NATIVE_MACD"

BACKTEST_WINDOW = {
    "start": "2026-09-01",
    "end": "2026-09-30",
    "option_warmup_start": "2026-08-20",
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
}

OPTION_SELECTION = {
    "underlying": "NIFTY",
    "strike_step": 50,
    "daily_atm_reference": "09:15_NIFTY_SPOT_OPEN",
    "fixed_strike_for_entire_session": True,
    "rights": ["CE", "PE"],
    "expiry_policy": "NEAREST_NON_EXPIRED_WEEKLY_INCLUDING_0DTE",
    "weekly_expiries": [
        "2026-09-01",
        "2026-09-08",
        "2026-09-15",
        "2026-09-22",
        "2026-09-29",
        "2026-10-06",
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
    "no_macd_parameter_optimization": True,
    "no_extra_filters": True,
    "no_stop_target_search": True,
    "strategy_d_remains_paused": True,
}
