"""Fresh April 2026 holdout for the frozen F5 quality-score>=2 candidate.

Candidate
---------
Keep the complete F5 setup and trade management unchanged, but permit a new
bullish entry only when at least TWO of the four already-frozen entry-time
quality states are true:

- ATR_ACTIVE
- BB_ACTIVE
- STOCH_NOT_OVERBOUGHT
- HIST_STRONG

The score>=2 cutoff was selected using the previously frozen 65% preservation
floor, not by maximizing Jul-Sep PnL. It retained 85.27% of baseline activated
signals and 79.83% of baseline winners on development data; score>=3 failed that
existing preservation floor.

April 2026 is untouched F5 holdout data. No threshold, score weight, side, time,
stop, target, or trail changes may be made after observing April.
"""

PROTOCOL_VERSION = "STRATEGY_F5_SCORE_GE2_APRIL_HOLDOUT_V1"
STRATEGY_ID = "F5"
CANDIDATE_NAME = "F5_QUALITY_SCORE_GE_2"
ROLE = "FRESH_HOLDOUT_VALIDATION"

WINDOW = {
    "start": "2026-04-01",
    "end": "2026-04-30",
    "warmup_start": "2026-03-20",
    "warmup_previous_sessions_per_contract": 5,
    "provider": "BREEZE",
    "source_interval": "1minute",
    "signal_interval_minutes": 2,
    "session_start": "09:15",
    "session_end_exclusive": "15:30",
    "last_entry_time": "15:15",
    "force_exit_time": "15:20",
}

# NIFTY weekly expiry is Tuesday; Apr 14 is an NSE trading holiday, so that
# week's contract expires on the previous trading day, Monday Apr 13.
EXPIRIES = [
    "2026-04-07",
    "2026-04-13",
    "2026-04-21",
    "2026-04-28",
    "2026-05-05",
]

ENTRY = {
    "base_rvi_length": 10,
    "base_rvi_threshold": 50.0,
    "base_macd_fast": 12,
    "base_macd_slow": 26,
    "base_macd_signal": 9,
    "quality_score_min": 2,
    "score_components": [
        "ATR_ACTIVE",
        "BB_ACTIVE",
        "STOCH_NOT_OVERBOUGHT",
        "HIST_STRONG",
    ],
    "atr_active": "ATR14_PCT_GE_PRIOR20_BAR_MEDIAN",
    "bb_active": "BB20_2_BANDWIDTH_PCT_GE_PRIOR20_BAR_MEDIAN",
    "stoch_not_overbought": "STOCH_D3_LT_80",
    "hist_strong": "MACD_HIST_PCT_GE_0_15",
    "development_source": "2026-07_THROUGH_2026-09_FOUR_FACTOR_SCORE",
}

VALIDATION_GATE = {
    "minimum_candidate_trades": 60,
    "minimum_candidate_trades_each_side": 20,
    "minimum_baseline_trail_activations": 15,
    "minimum_baseline_activated_signal_capture_pct": 65.0,
    "minimum_baseline_winner_signal_capture_pct": 65.0,
    "require_candidate_activation_rate_above_baseline": True,
    "require_candidate_net_positive_zero_slippage": True,
    "require_candidate_profit_factor_above_one_zero_slippage": True,
    "require_candidate_net_positive_half_point_slippage": True,
    "require_candidate_net_above_baseline_at_all_slippages": True,
    "require_candidate_max_drawdown_below_baseline_zero_slippage": True,
    "purpose": "fresh_single_holdout_gate_not_live_validation",
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "score_cutoff_frozen_before_april_data": True,
    "no_score_cutoff_search_on_april": True,
    "no_score_weight_search_on_april": True,
    "no_component_threshold_search_on_april": True,
    "no_side_filter": True,
    "no_time_filter": True,
    "no_stop_or_target_search": True,
    "keep_existing_f5_trail_unchanged": True,
    "strategy_d_remains_paused": True,
}
