"""Frozen July 2026 replication protocol for both Strategy F2 variants.

This protocol is frozen after inspecting the August 2026 F2 development
backtest. Both predeclared confirmation depths are carried forward unchanged.
No option-side filtering, MACD tuning, stop/target search, or extra filters are
allowed.

Replication month:
- 2026-07-01 through 2026-07-31
- MACD warmup begins 2026-06-19
"""
from __future__ import annotations

PROTOCOL_VERSION = "STRATEGY_F2_JULY_REPLICATION_V1"
STRATEGY_ID = "F2"
STRATEGY_NAME = "MACD_HISTOGRAM_PERSISTENCE"
ROLE = "SEPARATE_MONTH_HISTORICAL_REPLICATION"

BACKTEST_WINDOW = {
    "start": "2026-07-01",
    "end": "2026-07-31",
    "warmup_start": "2026-06-19",
    "provider": "BREEZE",
    "bar_interval": "5minute",
    "session_start": "09:15",
    "force_exit_time": "15:20",
}

MACD = {
    "fast_length": 12,
    "slow_length": 26,
    "signal_length": 9,
    "source": "close",
    "ema_adjust": False,
    "continuous_across_sessions": True,
    "histogram": "macd_minus_signal",
}

VARIANTS = {
    "F2_1BAR": {
        "confirmation_bars": 1,
        "histogram_same_side": True,
        "absolute_histogram_strictly_expands_each_confirmation_bar": True,
    },
    "F2_2BAR": {
        "confirmation_bars": 2,
        "histogram_same_side": True,
        "absolute_histogram_strictly_expands_each_confirmation_bar": True,
    },
}

OPTION_SELECTION = {
    "underlying": "NIFTY",
    "rights": {"bullish": "CE", "bearish": "PE"},
    "strike_step": 50,
    "moneyness": "ATM_AT_FINAL_CONFIRMATION_CLOSE",
    "atm_rounding": "HALF_UP",
    "expiry_policy": "NEAREST_NON_EXPIRED_WEEKLY_INCLUDING_0DTE",
    "weekly_expiries": [
        "2026-07-07",
        "2026-07-14",
        "2026-07-21",
        "2026-07-28",
        "2026-08-04",
    ],
    "lot_size": 65,
}

EXECUTION = {
    "one_position_at_a_time_per_variant": True,
    "entry_price": "NEXT_5M_OPTION_BAR_OPEN_AFTER_FINAL_CONFIRMATION",
    "exit_signal": "OPPOSITE_RAW_MACD_CROSSOVER",
    "exit_price": "NEXT_5M_OPTION_BAR_OPEN",
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

REPLICATION_REPORT = {
    "report_both_variants": True,
    "report_calls_and_puts_separately": True,
    "report_slippage_sensitivity": True,
    "report_largest_winner_concentration": True,
    "report_trade_price_coverage": True,
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "both_august_variants_must_be_carried_forward": True,
    "no_confirmation_depth_selection_before_july_scoring": True,
    "no_option_side_filter": True,
    "no_macd_parameter_optimization": True,
    "no_extra_filters": True,
    "no_stop_target_search": True,
    "strategy_d_remains_paused": True,
}
