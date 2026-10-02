"""Frozen Strategy F2 exploration: MACD crossover with histogram persistence.

This is a new research/backtest variant created after Strategy F V1 failed in
September 2026. September is NOT reused for scoring or tuning F2.

Development month:
- 2026-08-01 through 2026-08-31
- warmup begins 2026-07-20 for continuous 5-minute MACD state

Shared signal:
- NIFTY spot 5-minute close
- MACD(12, 26, 9), EMA adjust=False, continuous across sessions
- bullish crossover: MACD line crosses above signal line
- bearish crossover: MACD line crosses below signal line

Entry variants:
F2_1BAR:
- after crossover, wait for one completed 5-minute confirmation bar
- histogram must remain on the crossover side
- absolute histogram must strictly exceed the crossover-bar absolute histogram
- entry at the following option-bar OPEN

F2_2BAR:
- after crossover, wait for two consecutive completed confirmation bars
- histogram must remain on the crossover side on both bars
- absolute histogram must strictly expand on each bar
- entry at the following option-bar OPEN

Exit:
- opposite RAW MACD crossover exits at the next option-bar OPEN
- no confirmation delay on exits
- force flat at 15:20 IST
- no stop, target, RSI, zero-line filter, or parameter optimization
"""
from __future__ import annotations

PROTOCOL_VERSION = "STRATEGY_F2_MACD_HISTOGRAM_PERSISTENCE_V1"
STRATEGY_ID = "F2"
STRATEGY_NAME = "MACD_HISTOGRAM_PERSISTENCE"

BACKTEST_WINDOW = {
    "start": "2026-08-01",
    "end": "2026-08-31",
    "warmup_start": "2026-07-20",
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
        "2026-08-04",
        "2026-08-11",
        "2026-08-18",
        "2026-08-25",
        "2026-09-01",
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

COMPARISON = {
    "primary": "NET_PNL_AND_PROFIT_FACTOR_AFTER_EXPLICIT_COSTS",
    "report_each_variant_separately": True,
    "do_not_choose_or_tune_confirmation_depth_on_september": True,
    "august_is_exploratory_development_month": True,
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "no_macd_parameter_optimization": True,
    "no_extra_filters": True,
    "no_stop_target_search": True,
    "september_2026_not_used_for_f2_scoring": True,
    "strategy_d_remains_paused": True,
}
