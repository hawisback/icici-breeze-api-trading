"""Frozen June 2026 PE-only replication for Strategy F3 option-native MACD.

This protocol is frozen only after the PE-side asymmetry replicated independently
in August after first being observed in September.

No parameter, strike, timing, exit, stop, or target change is allowed. The only
candidate restriction is PE-only, predeclared before June scoring.
"""
from __future__ import annotations

PROTOCOL_VERSION = "STRATEGY_F3_PE_JUNE_REPLICATION_V1"
STRATEGY_ID = "F3_PE"
STRATEGY_NAME = "OPTION_NATIVE_MACD_PE_ONLY"
ROLE = "SEPARATE_MONTH_PE_ONLY_HISTORICAL_REPLICATION"

BACKTEST_WINDOW = {
    "start": "2026-06-01",
    "end": "2026-06-30",
    "option_warmup_start": "2026-05-20",
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
    "rights": ["PE"],
    "expiry_policy": "NEAREST_NON_EXPIRED_WEEKLY_INCLUDING_0DTE",
    "weekly_expiries": [
        "2026-06-02",
        "2026-06-09",
        "2026-06-16",
        "2026-06-23",
        "2026-06-30",
        "2026-07-07",
    ],
    "lot_size": 65,
}

SIGNALS = {
    "entry": "PE_MACD_PREV <= PE_SIGNAL_PREV AND PE_MACD_NOW > PE_SIGNAL_NOW",
    "exit": "PE_MACD_PREV >= PE_SIGNAL_PREV AND PE_MACD_NOW < PE_SIGNAL_NOW",
    "signal_known_at": "completed_option_5m_bar_end",
}

EXECUTION = {
    "one_pe_position_at_a_time": True,
    "entry_price": "NEXT_5M_PE_BAR_OPEN",
    "exit_price": "NEXT_5M_PE_BAR_OPEN",
    "force_exit_price": "15:20_PE_BAR_OPEN",
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

REPLICATION_GATE = {
    "net_pnl_after_explicit_costs_positive": True,
    "profit_factor_above_one": True,
    "net_pnl_positive_at_0_5_point_slippage_each_side": True,
    "net_pnl_positive_at_1_0_point_slippage_each_side": True,
    "full_trade_price_coverage_required": True,
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "pe_only_predeclared_before_june_scoring": True,
    "no_dynamic_atm_splicing": True,
    "no_macd_parameter_optimization": True,
    "no_extra_filters": True,
    "no_time_of_day_filter": True,
    "no_stop_target_search": True,
    "strategy_d_remains_paused": True,
}
