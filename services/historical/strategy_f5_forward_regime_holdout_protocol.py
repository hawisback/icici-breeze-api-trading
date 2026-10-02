"""Fresh forward holdout for F5 entry-time dominant-NIFTY context.

This protocol is frozen on 2026-10-02 before any holdout session is observed.
October 1 is explicitly excluded because it was already inspected in earlier
F5 dominant-regime research. October 2 is an NSE holiday, so the first clean
forward session is October 5.

The Jul-Sep development program found one market-context relationship that was
consistent enough to justify fresh validation: on whole-day BEARISH NIFTY
regime sessions, baseline F5 PE trades were positive while CE trades were
negative in each development month.

This holdout converts that descriptive hindsight label into a no-lookahead
entry-time context diagnostic without changing the baseline F5 trade path:
- anchor to the 09:15 NIFTY spot open,
- use only completed NIFTY 5-minute bars available before each F5 entry,
- vote on current net location, median location and OLS slope,
- BEARISH only when all three votes are bearish,
- BULLISH only when all three votes are bullish,
- otherwise MIXED,
- no magnitude threshold.

Cash flow, breadth, participant OI and futures-state layers are NOT carried
into the primary holdout because they did not earn stable standalone rule
status in Jul-Sep.

This is matched-trade validation only. It does not resimulate a filtered path
and does not promote a live or paper rule.
"""
from __future__ import annotations

PROTOCOL_VERSION = "STRATEGY_F5_FORWARD_REGIME_HOLDOUT_V1"
STRATEGY_ID = "F5"
ROLE = "FRESH_FORWARD_HOLDOUT_NO_LOOKAHEAD_CONTEXT_VALIDATION"
FROZEN_ON = "2026-10-02"
FROZEN_LATE_ENTRY_DIAGNOSTIC_CUTOFF = "14:30"

WINDOW = {
    "start": "2026-10-05",
    "end": "2026-12-29",
    "warmup_start": "2026-09-25",
    "months": ["2026-10", "2026-11", "2026-12"],
    "session_start": "09:15",
    "session_end_exclusive": "15:30",
    "spot_interval_minutes": 5,
    "option_interval": "1minute",
    "signal_interval_minutes": 2,
    "warmup_previous_sessions_per_contract": 5,
}

EXCLUDED_PREVIOUSLY_INSPECTED_DATES = ["2026-10-01"]

# NIFTY weekly options expire Tuesday; when Tuesday is an NSE trading holiday,
# expiry moves to the previous trading day. The holdout ends on the final frozen
# 2026 weekly expiry so no Jan-2027 contract assumption is required.
EXPIRIES = [
    "2026-10-06",
    "2026-10-13",
    "2026-10-19",
    "2026-10-27",
    "2026-11-03",
    "2026-11-09",
    "2026-11-17",
    "2026-11-23",
    "2026-12-01",
    "2026-12-08",
    "2026-12-15",
    "2026-12-22",
    "2026-12-29",
]

OPTION_SELECTION = {
    "underlying": "NIFTY",
    "strike_step": 50,
    "daily_atm_reference": "09:15_NIFTY_SPOT_OPEN",
    "fixed_strike_for_entire_session": True,
    "rights": ["CE", "PE"],
    "expiry_policy": "NEAREST_NON_EXPIRED_WEEKLY_INCLUDING_0DTE",
    "weekly_expiries": EXPIRIES,
    "lot_size": 65,
}

ENTRY_TIME_REGIME = {
    "anchor": "09:15_NIFTY_SPOT_OPEN",
    "information_set": "COMPLETED_5MIN_BARS_STRICTLY_AVAILABLE_BEFORE_F5_ENTRY",
    "minimum_completed_5m_bars": 2,
    "net_return_vote": (
        "SIGN_OF_LAST_COMPLETED_5M_CLOSE_MINUS_0915_OPEN"
    ),
    "median_location_vote": (
        "SIGN_OF_MEDIAN_COMPLETED_5M_CLOSE_MINUS_0915_OPEN"
    ),
    "session_slope_vote": "SIGN_OF_OLS_SLOPE_COMPLETED_5M_CLOSE",
    "bullish": "ALL_THREE_VOTES_BULLISH",
    "bearish": "ALL_THREE_VOTES_BEARISH",
    "mixed": "ANY_OTHER_COMBINATION",
    "magnitude_threshold": None,
}

PRIMARY_HYPOTHESIS = {
    "population": "MATCHED_BASELINE_F5_TRADES_WITH_ENTRY_TIME_BEARISH_REGIME",
    "comparison": "PE_VS_CE",
    "development_basis": (
        "Jul-Sep whole-day bearish regime: PE positive and CE negative in every "
        "development month; no symmetric bullish rule was established."
    ),
    "not_a_filtered_path_backtest": True,
}

VALIDATION_GATE = {
    "expected_target_sessions": 58,
    "holdout_end_session_required": True,
    "minimum_bearish_PE_trades": 20,
    "minimum_bearish_CE_trades": 10,
    "require_bearish_PE_net_positive": True,
    "require_bearish_CE_net_negative": True,
    "require_bearish_PE_average_pnl_above_CE": True,
    "require_bearish_PE_activation_rate_above_CE": True,
    "minimum_comparable_bearish_days": 5,
    "minimum_pct_bearish_days_PE_net_above_CE": 55.0,
    "if_coverage_fails": "INCONCLUSIVE_NOT_FAIL",
    "no_gate_retuning_after_holdout_opens": True,
    "blind_partial_window_outcomes": True,
}

SECONDARY_REPORTING = {
    "bullish_CE_vs_PE": "DESCRIPTIVE_ONLY",
    "mixed_regime": "DESCRIPTIVE_ONLY",
    "entry_time_regime_availability": True,
    "monthly_stability": True,
    "late_bearish_PE_ge_1430": (
        "DESCRIPTIVE_ONLY_PREREGISTERED_AFTER_JUNE_HOLDOUT_FAILURE"
    ),
    "late_entry_cutoff": FROZEN_LATE_ENTRY_DIAGNOSTIC_CUTOFF,
}

SOURCE_PROVENANCE = {
    "nse_expiry_rule": (
        "NIFTY weekly options expire every Tuesday; if Tuesday is a trading "
        "holiday, expiry is the previous trading day."
    ),
    "2026_holdout_holidays_relevant_to_tuesday_expiry": [
        "2026-10-20",
        "2026-11-10",
        "2026-11-24",
    ],
    "nifty_lot_size": 65,
}

GUARDRAILS = {
    "research_only": True,
    "backtest_only": True,
    "live_execution": False,
    "paper_execution": False,
    "broker_orders": False,
    "fresh_forward_holdout": True,
    "october_1_excluded_as_previously_inspected": True,
    "no_future_information_at_entry": True,
    "matched_baseline_trades_only": True,
    "no_filtered_path_resimulation": True,
    "no_regime_magnitude_threshold": True,
    "no_threshold_search_on_holdout": True,
    "no_partial_holdout_outcome_reporting": True,
    "late_entry_forward_reporting_descriptive_only": True,
    "no_late_entry_hard_block": True,
    "no_cash_filter": True,
    "no_breadth_filter": True,
    "no_institutional_oi_filter": True,
    "no_futures_state_filter": True,
    "no_composite_score": True,
    "keep_existing_f5_entry_exit_trail_unchanged": True,
    "strategy_d_remains_paused": True,
}
