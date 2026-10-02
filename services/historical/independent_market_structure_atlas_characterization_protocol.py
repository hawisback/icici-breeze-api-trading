"""Frozen same-corpus characterization of two atlas discoveries.

This protocol is intentionally NOT an independent replication. The two
relationships were selected because they met the frozen discovery labels in
NIFTY_BREEZE_MARKET_STRUCTURE_ATLAS_V1. This follow-up only characterizes their
shape and regime stability on the same inspected Breeze corpus.

Questions
---------
1. Does remaining-session range rise monotonically across quartiles of the
   first-30-minute range?
2. Does current-session range rise monotonically across quartiles of the
   previous session's range?
3. Are both relationships positive in both pre-September-2025 and
   September-2025-onward contract-expiry eras?

No threshold selection, direction, P&L, sizing, or implementation is allowed.
"""
from __future__ import annotations

PROTOCOL_VERSION = "NIFTY_BREEZE_ATLAS_PATTERN_CHARACTERIZATION_V1"
CORPUS_ROLE = "SAME_CORPUS_POST_DISCOVERY_CHARACTERIZATION_NOT_VALIDATION"

SOURCE = {
    "database_sha256": (
        "3e75de732077aa056c4027ed20f789059f11787fc7102c2de9a6a6ce04cbeee6"
    ),
    "window": ["2025-01-01", "2026-09-18"],
    "common_session_slice": ["09:15", "15:25"],
    "bars_per_session": 75,
    "selected_from_atlas_protocol": "NIFTY_BREEZE_MARKET_STRUCTURE_ATLAS_V1",
    "selected_patterns": [
        "opening_range_vs_remaining_range",
        "daily_range_persistence",
    ],
}

QUARTILE_CHARACTERIZATION = {
    "bins": 4,
    "construction": (
        "empirical quartiles of the predictor on the complete usable same-corpus "
        "sample; report predictor cut points only descriptively"
    ),
    "statistics": [
        "sessions",
        "predictor_mean_bps",
        "outcome_mean_bps",
        "outcome_median_bps",
    ],
    "shape_metrics": [
        "adjacent_outcome_means_strictly_increasing",
        "adjacent_outcome_medians_strictly_increasing",
        "q4_to_q1_outcome_mean_ratio",
        "q4_minus_q1_outcome_mean_bps",
    ],
}

REGIMES = {
    "pre_tuesday_expiry_era": ["2025-01-01", "2025-08-31"],
    "tuesday_expiry_era": ["2025-09-01", "2026-09-18"],
}

RELATIONSHIPS = {
    "opening_range_vs_remaining_range": {
        "predictor": "first_30m_high_low_range_bps",
        "outcome": "post_09_40_remaining_session_high_low_range_bps",
    },
    "daily_range_persistence": {
        "predictor": "previous_session_high_low_range_bps",
        "outcome": "session_high_low_range_bps",
    },
}

GUARDRAILS = {
    "research_only": True,
    "same_corpus_characterization": True,
    "independent_validation": False,
    "blind_validation": False,
    "candidate_freeze": False,
    "implementation_allowed": False,
    "pnl_scored": False,
    "directional_entry_exit_rule": False,
    "threshold_optimization": False,
    "quartiles_are_descriptive_not_trading_thresholds": True,
    "no_posthoc_extra_buckets_after_results": True,
    "strategy_d_remains_paused": True,
}
