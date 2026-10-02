"""Frozen research protocol for Strategy F: MACD Call/Put intraday backtest.

Strategy F is research/backtest only. It is deliberately NOT registered in the
production strategy runtime and has no PAPER/LIVE execution path.

Signal:
- NIFTY spot 5-minute close bars from Breeze.
- MACD fast EMA 12, slow EMA 26, signal EMA 9, source=close.
- Bullish MACD-line crossover above signal-line => buy ATM NIFTY CE.
- Bearish MACD-line crossover below signal-line => buy ATM NIFTY PE.

Execution:
- A crossover is known only after its 5-minute signal bar closes.
- Entry uses the next 5-minute option bar OPEN.
- Nearest non-expired weekly NIFTY option expiry, including 0DTE.
- ATM strike uses deterministic half-up rounding to the nearest 50 points.
- One position at a time.
- Opposite crossover exits the current option at the next 5-minute option bar
  OPEN. If before force-exit, the opposite option may enter at that same bar.
- No stop, target, zero-line filter, RSI filter, or MACD parameter tuning.
- Any open position is exited at the 15:20 IST option-bar OPEN.

Window:
- Previous complete calendar month at freeze time: 2026-09-01..2026-09-30.
- Warmup starts 2026-08-20 and is used only to initialize continuous MACD.
"""
from __future__ import annotations

PROTOCOL_VERSION = "STRATEGY_F_MACD_OPTIONS_V1"
STRATEGY_ID = "F"
STRATEGY_NAME = "MACD_CALL_PUT_CROSSOVER"
ROLE = "RESEARCH_BACKTEST_ONLY"

BACKTEST_WINDOW = {
    "start": "2026-09-01",
    "end": "2026-09-30",
    "warmup_start": "2026-08-20",
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
}

SIGNAL = {
    "bullish": "macd_prev <= signal_prev AND macd_now > signal_now",
    "bearish": "macd_prev >= signal_prev AND macd_now < signal_now",
    "signal_known_at": "completed_5m_bar_end",
    "underlying": "NIFTY_SPOT",
}

OPTION_SELECTION = {
    "underlying": "NIFTY",
    "rights": {"bullish": "CE", "bearish": "PE"},
    "strike_step": 50,
    "moneyness": "ATM_AT_SIGNAL_CLOSE",
    "atm_rounding": "HALF_UP",
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

EXECUTION = {
    "one_position_at_a_time": True,
    "entry_price": "NEXT_5M_OPTION_BAR_OPEN",
    "opposite_crossover_exit_price": "NEXT_5M_OPTION_BAR_OPEN",
    "force_exit_price": "15:20_OPTION_BAR_OPEN",
    "reverse_on_opposite_crossover_before_force_exit": True,
    "allow_new_entry_at_or_after_force_exit": False,
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
    "trade_list": True,
    "gross_and_net_pnl": True,
    "premium_return_pct": True,
    "call_put_split": True,
    "daily_pnl": True,
    "time_of_day_split": True,
    "win_rate": True,
    "profit_factor": True,
    "max_drawdown": True,
    "average_hold_minutes": True,
    "slippage_sensitivity": True,
    "data_coverage_and_skips": True,
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "no_parameter_optimization": True,
    "no_posthoc_signal_filters": True,
    "strategy_d_remains_paused": True,
}
