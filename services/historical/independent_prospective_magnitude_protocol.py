"""Frozen prospective NIFTY movement-magnitude replication protocol.

This protocol is frozen before any session in the prospective sample is
collected or scored. It asks one descriptive, non-directional question:

Does the exact previously replicated relationship between trailing 30-minute
NIFTY futures range and the next 30-minute maximum absolute NIFTY futures
excursion replicate again on a genuinely forward sample?

The protocol deliberately does not define a trading strategy, direction,
threshold, position size, P&L rule, or implementation path.

Calendar basis verified from NSE before freeze on 2026-09-30:
- 2026 F&O holidays include 2026-10-02, 2026-10-20, 2026-11-10 and
  2026-11-24;
- NIFTY futures monthly contracts expire on the last Tuesday of the expiry
  month, or the previous trading day when that Tuesday is a trading holiday;
- equity-derivatives regular trading is 09:15-15:40.

Therefore the exact prospective sample is the first 30 eligible completed
sessions strictly after 2026-09-30: 2026-10-01 through 2026-11-16.
The October contract expires 2026-10-27. The November contract expires
2026-11-23 because 2026-11-24 is an NSE F&O holiday.

No findings may be computed until all 30 sessions are complete and the full
market artifact passes exact-shape QA.
"""
from __future__ import annotations

PROTOCOL_VERSION = "NIFTY_PROSPECTIVE_MAGNITUDE_REPLICATION_V1"
FREEZE_DATE = "2026-09-30"

NSE_VERIFICATION = {
    "verified_on": FREEZE_DATE,
    "fno_holiday_source": (
        "NSE/FAOP/71777 - Trading holidays for the calendar year 2026"
    ),
    "contract_specification_source": (
        "NSE NIFTY 50 F&O contract specifications / expiry rule"
    ),
    "market_timing_source": "NSE Market Timings & Holidays - derivatives segment",
}

SESSION_DATES = [
    "2026-10-01",
    "2026-10-05",
    "2026-10-06",
    "2026-10-07",
    "2026-10-08",
    "2026-10-09",
    "2026-10-12",
    "2026-10-13",
    "2026-10-14",
    "2026-10-15",
    "2026-10-16",
    "2026-10-19",
    "2026-10-21",
    "2026-10-22",
    "2026-10-23",
    "2026-10-26",
    "2026-10-27",
    "2026-10-28",
    "2026-10-29",
    "2026-10-30",
    "2026-11-02",
    "2026-11-03",
    "2026-11-04",
    "2026-11-05",
    "2026-11-06",
    "2026-11-09",
    "2026-11-11",
    "2026-11-12",
    "2026-11-13",
    "2026-11-16",
]

EXCLUDED_FNO_HOLIDAYS_WITHIN_WINDOW = [
    "2026-10-02",
    "2026-10-20",
    "2026-11-10",
]

BLOCKS = {
    "block1": SESSION_DATES[:10],
    "block2": SESSION_DATES[10:20],
    "block3": SESSION_DATES[20:],
}

ROLL_SCHEDULE = [
    {
        "first_session": "2026-10-01",
        "last_session": "2026-10-27",
        "futures_expiry": "2026-10-27",
    },
    {
        "first_session": "2026-10-28",
        "last_session": "2026-11-16",
        "futures_expiry": "2026-11-23",
    },
]

CONTRACT_BY_DATE = {
    **{day: "2026-10-27" for day in SESSION_DATES[:17]},
    **{day: "2026-11-23" for day in SESSION_DATES[17:]},
}

SESSION_START = "09:15"
SESSION_END_EXCLUSIVE = "15:40"
EXPECTED_BARS_PER_SESSION = 77
EXPECTED_ROWS = len(SESSION_DATES) * EXPECTED_BARS_PER_SESSION

PREDICTOR = {
    "name": "trailing_30m_range_bps",
    "definition": (
        "(max high - min low) over current+prior five 5m bars, divided by "
        "current 5m close, times 10000"
    ),
    "bars": 6,
}

TARGET = {
    "name": "next_30m_max_absolute_excursion_bps",
    "definition": (
        "max(next six 5m highs - current close, current close - next six 5m lows) "
        "divided by current close, times 10000"
    ),
    "future_bars": 6,
}

EXPECTED_SCORABLE_EVENTS_PER_SESSION = EXPECTED_BARS_PER_SESSION - 5 - 6
EXPECTED_SCORABLE_EVENTS = (
    len(SESSION_DATES) * EXPECTED_SCORABLE_EVENTS_PER_SESSION
)

BOOTSTRAP = {
    "draws": 10000,
    "seed": 20261003,
    "cluster": "session",
    "interval": "percentile_95",
}

REPLICATION_GATE = {
    # Exact complete-window QA is mandatory, so a partial prospective sample
    # cannot satisfy the event-count gate.
    "minimum_scorable_events": EXPECTED_SCORABLE_EVENTS,
    "pooled_spearman_must_be_positive": True,
    "all_three_block_spearman_must_be_positive": True,
    "session_cluster_bootstrap_95pct_lower_must_be_positive": True,
}

COLLECTION_AND_SCORING_POLICY = {
    "provider": "BREEZE",
    "session_may_be_collected_only_after_regular_session_completed": True,
    "regular_session_completion_time_ist": SESSION_END_EXCLUSIVE,
    "partial_session_collection_for_scoring_forbidden": True,
    "score_before_all_30_sessions_complete": False,
    "full_sample_not_complete_before": "2026-11-16T15:40:00+05:30",
    "qa_only_before_full_sample_completion": True,
    "no_outcome_inspection_before_full_sample_completion": True,
}

FORBIDDEN_FEATURES = [
    "direction",
    "pnl",
    "quartile_thresholds",
    "range_cutoffs",
    "position_sizing",
    "options",
    "vix",
    "volume_filters",
    "oi_filters",
    "time_of_day_filters",
    "dte_filters",
    "other_features",
]

TERMINAL_OUTCOMES = {
    "if_fail": (
        "RETIRE_MAGNITUDE_THESIS_NO_FURTHER_TUNING_OR_RESCUE"
    ),
    "if_pass": (
        "NO_TRADING_PROMOTION; ONLY_A_SEPARATE_FORECASTING_MODEL_PROJECT_ON_"
        "GENUINELY_NEW_DEVELOPMENT_DATA_MAY_BE_STARTED"
    ),
}

GUARDRAILS = {
    "research_only": True,
    "descriptive_only": True,
    "prospective_sample": True,
    "candidate_frozen": False,
    "blind_validation_opened": False,
    "implementation_allowed": False,
    "provider": "BREEZE",
    "new_broker_or_trading_api_used": False,
    "directional_claim": False,
    "pnl_scored": False,
    "threshold_selection": False,
    "position_sizing_rule": False,
    "options_used": False,
    "vix_used": False,
    "volume_filter": False,
    "oi_filter": False,
    "time_of_day_filter": False,
    "dte_filter": False,
    "no_parameter_changes_after_freeze": True,
    "no_rescue_after_results": True,
    "pass_does_not_create_trading_candidate": True,
    "pass_does_not_authorize_blind_validation": True,
    "pass_does_not_authorize_implementation": True,
    "strategy_d_remains_paused": True,
}
