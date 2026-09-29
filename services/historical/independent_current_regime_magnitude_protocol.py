"""Frozen current-regime NIFTY movement-magnitude replication pilot.

This is not a trading-strategy search. It asks one descriptive question:
does recent 30-minute NIFTY futures range rank-order the next 30-minute maximum
absolute futures excursion in a genuinely new, current-session-regime sample?

The old research corpus used 75 five-minute bars/day. Current NSE equity
derivatives regular trading runs 09:15-15:40, so this pilot is isolated from the
old corpus and uses 77 five-minute bars/day. The shared historical provider
normalizer is not modified.

Frozen before collection:
- provider: BREEZE only (already user-authorized);
- sessions: 13 completed sessions, 2026-09-10 through 2026-09-29;
- 2026-09-14 excluded as NSE F&O trading holiday;
- contract: NIFTY futures expiring 2026-09-29;
- one predictor, one target, no directional sign and no P&L.
"""
from __future__ import annotations

PROTOCOL_VERSION = "NIFTY_CURRENT_REGIME_MAGNITUDE_REPLICATION_V1"

SESSION_DATES = [
    "2026-09-10",
    "2026-09-11",
    "2026-09-15",
    "2026-09-16",
    "2026-09-17",
    "2026-09-18",
    "2026-09-21",
    "2026-09-22",
    "2026-09-23",
    "2026-09-24",
    "2026-09-25",
    "2026-09-28",
    "2026-09-29",
]

BLOCKS = {
    "block1": SESSION_DATES[:5],
    "block2": SESSION_DATES[5:9],
    "block3": SESSION_DATES[9:],
}

FUTURES_EXPIRY = "2026-09-29"
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

EXPECTED_SCORABLE_EVENTS = len(SESSION_DATES) * (
    EXPECTED_BARS_PER_SESSION - 5 - 6
)

BOOTSTRAP = {
    "draws": 10000,
    "seed": 20261003,
    "cluster": "session",
    "interval": "percentile_95",
}

REPLICATION_GATE = {
    "minimum_scorable_events": 800,
    "pooled_spearman_must_be_positive": True,
    "all_three_block_spearman_must_be_positive": True,
    "session_cluster_bootstrap_95pct_lower_must_be_positive": True,
}

GUARDRAILS = {
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "provider": "BREEZE",
    "new_broker_or_trading_api_used": False,
    "directional_claim": False,
    "pnl_scored": False,
    "threshold_selection": False,
    "volume_filter": False,
    "oi_filter": False,
    "options_filter": False,
    "vix_filter": False,
    "no_parameter_changes_after_collection": True,
    "pass_does_not_create_trading_candidate": True,
    "strategy_d_remains_paused": True,
}
