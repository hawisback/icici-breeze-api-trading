"""Frozen retrospective robustness protocol for NIFTY movement magnitude.

Purpose
-------
Test the already-defined non-directional relationship between trailing
30-minute NIFTY futures range and next-30-minute maximum absolute excursion on
an older Breeze backtest window that predates every short-swing development
cohort used later in the research.

This is retrospective robustness evidence, not prospective validation and not
a trading-strategy search.

The sample window is frozen before outcome computation:
- 2025-01-01 through 2025-05-14 inclusive;
- it ends immediately before QA-amended Development Cohort 4 begins on
  2025-05-15;
- use the near-month NIFTY futures contract schedule already encoded in the
  Breeze backfill;
- use the legacy historical research slice 09:15 through 15:25 inclusive
  (75 five-minute bars). The backfill may also contain a 15:30 bar; that bar is
  deliberately ignored so this study does not alter legacy research semantics.

Every session in the fixed date window is eligible solely by data QA. No
outcome value may affect inclusion.
"""
from __future__ import annotations

PROTOCOL_VERSION = "NIFTY_RETROSPECTIVE_MAGNITUDE_ROBUSTNESS_V1"
CORPUS_ROLE = "RETROSPECTIVE_ROBUSTNESS_NOT_VALIDATION"

WINDOW_START = "2025-01-01"
WINDOW_END = "2025-05-14"

FUTURES_CONTRACT_PERIODS = [
    {
        "first_date": "2025-01-01",
        "last_date": "2025-01-30",
        "expiry": "2025-01-30",
        "instrument_id": "INST-NIFTY-FUT-2025-01-30",
    },
    {
        "first_date": "2025-01-31",
        "last_date": "2025-02-27",
        "expiry": "2025-02-27",
        "instrument_id": "INST-NIFTY-FUT-2025-02-27",
    },
    {
        "first_date": "2025-02-28",
        "last_date": "2025-03-27",
        "expiry": "2025-03-27",
        "instrument_id": "INST-NIFTY-FUT-2025-03-27",
    },
    {
        "first_date": "2025-03-28",
        "last_date": "2025-04-24",
        "expiry": "2025-04-24",
        "instrument_id": "INST-NIFTY-FUT-2025-04-24",
    },
    {
        "first_date": "2025-04-25",
        "last_date": "2025-05-14",
        "expiry": "2025-05-29",
        "instrument_id": "INST-NIFTY-FUT-2025-05-29",
    },
]

SESSION_START = "09:15"
SESSION_LAST_BAR = "15:25"
EXPECTED_BARS_PER_SESSION = 75
EXPECTED_SCORABLE_EVENTS_PER_SESSION = EXPECTED_BARS_PER_SESSION - 5 - 6

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

QA_POLICY = {
    "provider": "BREEZE",
    "interval": "5m",
    "all_complete_sessions_in_window": True,
    "outcome_independent_session_selection": True,
    "exact_75_bar_legacy_slice_required": True,
    "optional_15_30_bar_ignored": True,
    "valid_ohlc_required": True,
    "positive_prices_required": True,
    "volume_not_used_for_inclusion": True,
    "open_interest_not_used_for_inclusion": True,
    "minimum_complete_sessions": 60,
}

BLOCK_POLICY = {
    "type": "three_contiguous_chronological_blocks",
    "construction": (
        "split all QA-eligible sessions, in chronological order, into three "
        "contiguous blocks whose sizes differ by at most one; earlier blocks "
        "receive any remainder sessions"
    ),
}

BOOTSTRAP = {
    "draws": 10000,
    "seed": 20261003,
    "cluster": "session",
    "interval": "percentile_95",
}

REPLICATION_GATE = {
    "minimum_complete_sessions": 60,
    "minimum_scorable_events": (
        60 * EXPECTED_SCORABLE_EVENTS_PER_SESSION
    ),
    "pooled_spearman_must_be_positive": True,
    "all_three_block_spearman_must_be_positive": True,
    "session_cluster_bootstrap_95pct_lower_must_be_positive": True,
}

GUARDRAILS = {
    "research_only": True,
    "retrospective": True,
    "prospective_validation": False,
    "candidate_frozen": False,
    "blind_validation_opened": False,
    "implementation_allowed": False,
    "provider": "BREEZE",
    "directional_claim": False,
    "pnl_scored": False,
    "threshold_selection": False,
    "quartile_analysis": False,
    "position_sizing_rule": False,
    "options_used": False,
    "vix_used": False,
    "volume_filter": False,
    "oi_filter": False,
    "time_of_day_filter": False,
    "dte_filter": False,
    "no_outcome_based_session_selection": True,
    "no_parameter_tuning_after_results": True,
    "no_rescue_after_results": True,
    "does_not_replace_prospective_replication": True,
    "pass_does_not_create_trading_candidate": True,
    "pass_does_not_authorize_blind_validation": True,
    "pass_does_not_authorize_implementation": True,
    "strategy_d_remains_paused": True,
}
