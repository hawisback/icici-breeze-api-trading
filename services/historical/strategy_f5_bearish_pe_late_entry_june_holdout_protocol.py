"""Fresh June 2026 holdout for the F5 late bearish-PE entry risk candidate.

Development source
------------------
Jul-Sep bearish-regime PE was the strongest positive F5 context. A post-hoc
inspection after the frozen early-exit candidates failed found that entries at
or after 14:30 were unusually weak:
- 12 trades
- 1 winner
- 1 trail activation
- -Rs 5,472.69 net
- negative in Jul, Aug and Sep separately

Because 14:30 was discovered from development outcomes, it is NOT validated.
This protocol freezes exactly that single cutoff and forbids any June retuning.

Tradable holdout rule
---------------------
Keep baseline F5 unchanged except:
- for a new PE signal at or after 14:30,
- compute NIFTY regime using only completed 5-minute spot bars available at
  the signal decision timestamp,
- if all three untuned votes are BEARISH, suppress that PE entry,
- otherwise leave the signal unchanged.

Entry-time NIFTY votes:
1. last completed 5m close vs 09:15 open
2. median completed 5m close vs 09:15 open
3. OLS slope of completed 5m closes

No magnitude threshold is used. Existing F5 exits and the +10% close-confirmed
trail are unchanged.

June 2026 is an untouched F5 holdout for this exact late-entry question.
"""
from __future__ import annotations

PROTOCOL_VERSION = "STRATEGY_F5_BEARISH_PE_LATE_ENTRY_JUNE_HOLDOUT_V1"
STRATEGY_ID = "F5"
CANDIDATE_NAME = "ENTRY_TIME_BEARISH_PE_NO_NEW_ENTRY_GE_1430"
ROLE = "FRESH_HISTORICAL_HOLDOUT_VALIDATION"
FROZEN_CUTOFF = "14:30"

WINDOW = {
    "start": "2026-06-01",
    "end": "2026-06-30",
    "warmup_start": "2026-05-22",
    "provider": "BREEZE",
    "source_interval": "1minute",
    "signal_interval_minutes": 2,
    "spot_interval_minutes": 5,
    "session_start": "09:15",
    "session_end_exclusive": "15:30",
    "last_entry_time": "15:15",
    "force_exit_time": "15:20",
    "warmup_previous_sessions_per_contract": 5,
}

EXPIRIES = [
    "2026-06-02",
    "2026-06-09",
    "2026-06-16",
    "2026-06-23",
    "2026-06-30",
]

ENTRY_TIME_REGIME = {
    "anchor": "09:15_NIFTY_SPOT_OPEN",
    "minimum_completed_5m_bars": 2,
    "net_return_vote": "SIGN_OF_LAST_COMPLETED_5M_CLOSE_MINUS_0915_OPEN",
    "median_location_vote": (
        "SIGN_OF_MEDIAN_COMPLETED_5M_CLOSE_MINUS_0915_OPEN"
    ),
    "session_slope_vote": "SIGN_OF_OLS_SLOPE_COMPLETED_5M_CLOSE",
    "bearish": "ALL_THREE_VOTES_BEARISH",
    "bullish": "ALL_THREE_VOTES_BULLISH",
    "mixed": "ANY_OTHER_COMBINATION",
    "magnitude_threshold": None,
}

VALIDATION_GATE = {
    "minimum_selected_sessions": 20,
    "minimum_baseline_entry_time_bearish_PE_trades": 10,
    "minimum_baseline_late_entry_time_bearish_PE_trades": 3,
    "require_late_baseline_subset_net_negative": True,
    "minimum_baseline_target_winner_capture_pct": 95.0,
    "minimum_baseline_target_activation_capture_pct": 95.0,
    "require_candidate_full_path_net_above_baseline_at_0": True,
    "require_candidate_full_path_net_above_baseline_at_0_5": True,
    "require_candidate_full_path_net_above_baseline_at_1": True,
    "require_candidate_max_drawdown_below_baseline_at_0": True,
    "purpose": "single_fresh_holdout_gate_not_final_live_validation",
}

SOURCE_PROVENANCE = {
    "nifty_weekly_expiry_rule": (
        "Tuesday of expiry week; previous trading day if Tuesday is a holiday"
    ),
    "june_2026_exchange_holiday": "2026-06-26",
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "cutoff_frozen_before_june_scoring": True,
    "no_time_cutoff_search_on_june": True,
    "no_regime_threshold_search_on_june": True,
    "no_side_rule_search_on_june": True,
    "no_stop_or_target_search_on_june": True,
    "entry_time_regime_uses_no_future_information": True,
    "keep_existing_f5_exit_and_trail_unchanged": True,
    "strategy_d_remains_paused": True,
}
