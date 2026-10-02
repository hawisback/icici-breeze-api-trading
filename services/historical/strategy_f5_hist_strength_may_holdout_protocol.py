"""Fresh May 2026 holdout for the single F5 histogram-strength candidate.

The candidate was frozen after Jul-Sep entry-state diagnosis:
- exact option-chart 2-minute MACD(12,26,9)
- Relative Volatility Index(10) >= 50
- bullish MACD crossover
- normalized MACD histogram on signal bar >= 0.15% of option close
- next 2-minute bar open entry
- unchanged F5 trade management:
  * before +10% activation, bearish MACD crossover can exit
  * +10% completed-close return activates the existing trail
  * after activation, bearish MACD is ignored
  * trail remains 10 percentage points of initial trade capital behind the
    best completed 2-minute close return
  * close-confirmed breach, next 2-minute open exit
  * force flat 15:20

This May window is a fresh holdout and must not be used for threshold tuning.
"""
from __future__ import annotations

PROTOCOL_VERSION = "STRATEGY_F5_HIST_STRENGTH_MAY_HOLDOUT_V1"
STRATEGY_ID = "F5"
CANDIDATE_NAME = "F5_HIST_PCT_GE_0_15"
ROLE = "FRESH_HOLDOUT_VALIDATION"

WINDOW = {
    "start": "2026-05-04",
    "end": "2026-05-29",
    "warmup_start": "2026-04-22",
    "warmup_previous_sessions_per_contract": 5,
    "provider": "BREEZE",
    "source_interval": "1minute",
    "signal_interval_minutes": 2,
    "session_start": "09:15",
    "session_end_exclusive": "15:30",
    "last_entry_time": "15:15",
    "force_exit_time": "15:20",
}

EXPIRIES = [
    "2026-05-05",
    "2026-05-12",
    "2026-05-19",
    "2026-05-26",
    "2026-06-02",
]

ENTRY = {
    "rvi_length": 10,
    "rvi_threshold": 50.0,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "histogram_strength_pct_threshold": 0.15,
    "histogram_strength_definition": (
        "100 * (MACD - MACD_SIGNAL) / OPTION_CLOSE_ON_SIGNAL_BAR"
    ),
    "development_source": "2026-07_THROUGH_2026-09_ENTRY_STATE_DIAGNOSTIC",
}

VALIDATION_GATE = {
    "minimum_candidate_trades": 30,
    "minimum_baseline_trail_activations": 8,
    "minimum_baseline_activated_entry_capture_pct": 60.0,
    "require_candidate_activation_rate_above_baseline": True,
    "require_candidate_net_positive_zero_slippage": True,
    "require_candidate_profit_factor_above_one_zero_slippage": True,
    "require_candidate_net_positive_half_point_slippage": True,
    "require_candidate_net_above_baseline_at_all_slippages": True,
    "require_candidate_max_drawdown_below_baseline_zero_slippage": True,
    "purpose": "single_fresh_holdout_gate_not_final_live_validation",
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "threshold_frozen_before_may_data": True,
    "no_threshold_search_on_may": True,
    "no_side_filter": True,
    "no_time_filter": True,
    "no_stop_or_target_search": True,
    "keep_existing_f5_trail_unchanged": True,
    "strategy_d_remains_paused": True,
}
