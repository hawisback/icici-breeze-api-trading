"""Frozen exploratory protocol for a Breeze NIFTY futures market-structure atlas.

Purpose
-------
Use the existing historical Breeze NIFTY futures archive for pattern discovery.
This is explicitly exploratory research, not blind validation and not a trading
candidate screen. The protocol is frozen before atlas outputs are inspected.

The already-studied trailing-30m-range -> next-30m-excursion relationship is
excluded from this atlas so this project does not simply re-mine the known
magnitude thesis.

Common session slice
--------------------
Use 5-minute NIFTY futures bars from 09:15 through 15:25 inclusive (75 bars)
for cross-regime comparability. Later 15:30/15:35 bars, when available, are
ignored. Only exact 75-bar Breeze sessions with valid positive OHLC are used.

Frozen pattern families
-----------------------
1. Intraday volatility seasonality:
   mean absolute 5-minute return by bar index, plus opening/midday/late-session
   volatility ratios.
2. Overnight-gap magnitude vs same-day realized range.
3. First-30-minute realized range vs remaining-session realized range.
4. Session-to-session volatility persistence:
   previous session realized range vs current session realized range.
5. Day-of-week realized-range seasonality.
6. Days-to-expiry realized-range structure.

No directional entry/exit rule, threshold optimization, P&L, sizing, options,
VIX, volume/OI filter, or strategy implementation is part of this atlas.
"""
from __future__ import annotations

PROTOCOL_VERSION = "NIFTY_BREEZE_MARKET_STRUCTURE_ATLAS_V1"
CORPUS_ROLE = "EXPLORATORY_HISTORICAL_PATTERN_DISCOVERY_NOT_VALIDATION"

WINDOW = {
    "start": "2025-01-01",
    "end": "2026-09-18",
    "provider": "BREEZE",
    "interval": "5m",
    "session_start": "09:15",
    "session_last_bar": "15:25",
    "bars_per_session": 75,
    "common_slice_reason": (
        "use the common legacy 75-bar slice across the full historical window "
        "for cross-regime comparability"
    ),
}

PATTERN_FAMILIES = {
    "intraday_volatility_seasonality": {
        "bar_metric": "absolute_close_to_close_return_bps",
        "opening_window": ["09:20", "10:10"],
        "midday_window": ["11:30", "13:30"],
        "late_window": ["14:30", "15:25"],
    },
    "overnight_gap_vs_session_range": {
        "x": "absolute_open_vs_previous_close_gap_bps",
        "y": "session_high_low_range_bps",
    },
    "opening_range_vs_remaining_range": {
        "x": "first_30m_high_low_range_bps",
        "y": "post_09_40_remaining_session_high_low_range_bps",
    },
    "daily_range_persistence": {
        "x": "previous_session_high_low_range_bps",
        "y": "current_session_high_low_range_bps",
    },
    "weekday_range_seasonality": {
        "metric": "session_high_low_range_bps",
        "categories": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
    },
    "expiry_distance_range_structure": {
        "metric": "session_high_low_range_bps",
        "buckets_calendar_days": {
            "0_1": [0, 1],
            "2_5": [2, 5],
            "6_10": [6, 10],
            "11_plus": [11, 999],
        },
    },
}

CHRONOLOGICAL_ROBUSTNESS = {
    "blocks": 6,
    "construction": (
        "split QA-complete sessions into six contiguous chronological blocks "
        "whose sizes differ by at most one; earlier blocks receive remainder"
    ),
}

BOOTSTRAP = {
    "draws": 10000,
    "seed": 20260930,
    "unit": "session",
    "interval": "percentile_95",
}

DISCOVERY_LABELS = {
    "scalar_relationship": {
        "minimum_abs_pooled_spearman": 0.10,
        "minimum_same_sign_blocks": 5,
        "bootstrap_95pct_must_exclude_zero": True,
    },
    "categorical_structure": {
        "minimum_max_to_min_mean_ratio": 1.15,
        "minimum_blockwise_same_extreme_category": 4,
    },
    "intraday_seasonality": {
        "minimum_opening_to_midday_abs_return_ratio": 1.15,
        "minimum_late_to_midday_abs_return_ratio": 1.10,
    },
}

EXCLUDED_ALREADY_STUDIED_HYPOTHESES = [
    "trailing_30m_range_bps_vs_next_30m_max_absolute_excursion_bps",
]

GUARDRAILS = {
    "research_only": True,
    "exploratory": True,
    "blind_validation": False,
    "candidate_freeze": False,
    "implementation_allowed": False,
    "pnl_scored": False,
    "threshold_optimization": False,
    "directional_entry_exit_rule": False,
    "options_used": False,
    "vix_used": False,
    "volume_filter": False,
    "open_interest_filter": False,
    "known_magnitude_thesis_retested": False,
    "no_posthoc_feature_additions_after_results": True,
    "strategy_d_remains_paused": True,
}
