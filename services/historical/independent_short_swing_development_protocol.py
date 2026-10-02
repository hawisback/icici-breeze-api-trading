"""Protocol for combined Cohort-1 + Cohort-2 short-swing strategy development.

Both cohorts are already inspected development data. This protocol defines the
reproducible event table used to develop short-duration strategies; it does not
consume blind data, freeze a candidate, or authorize implementation.
"""
from __future__ import annotations

PROTOCOL_VERSION = "SHORT_SWING_DEVELOPMENT_V1"
CORPUS_ROLE = "INSPECTED_STRATEGY_DEVELOPMENT_NOT_VALIDATION"

COHORTS = {
    "cohort1": {
        "sessions": 80,
        "five_minute_rows": 6000,
        "one_minute_rows": 30000,
        "block_size": 10,
    },
    "cohort2": {
        "sessions": 72,
        "five_minute_rows": 5400,
        "one_minute_rows": 27000,
        "block_size": 12,
    },
}

ENTRY = {
    "signal_information_cutoff": "completed five-minute bar labelled t",
    "earliest_execution_proxy": "one-minute open at t+5 minutes",
    "reason": (
        "The five-minute bar labelled t aggregates one-minute bars t through "
        "t+4 and is complete only at t+5."
    ),
}

FIXED_EXIT_HORIZONS_MINUTES = (5, 10, 15, 30)

OUTCOMES = {
    "terminal": "entry-open to final one-minute close at the fixed horizon",
    "mfe_mae": (
        "maximum high and minimum low across all one-minute bars from entry "
        "through the fixed-horizon exit minute"
    ),
    "stops_targets": False,
    "same_bar_path_assumptions": False,
}

STRATEGY_FAMILIES = (
    "movement_conditioned_short_mean_reversion",
    "movement_conditioned_breakout_continuation",
    "failed_breakout_reversal",
)

FEATURE_POLICY = {
    "primary_futures_state": [
        "3-bar range",
        "6-bar range",
        "3-bar close location",
        "3-bar and 6-bar net return",
        "3-bar and 6-bar path length",
        "current futures return",
        "futures volume versus prior 3-bar mean",
        "futures OI change",
        "prior 3-bar high/low breakout and failed-breakout state",
    ],
    "options_fast_lead_role": (
        "entry-timing or bad-trade filter only; not a standalone directional strategy"
    ),
    "vix_role": "context/descriptive only; its frozen incremental replication failed",
    "atm_straddle_role": "descriptive only; its frozen incremental replication failed",
}

DEVELOPMENT_RULES = {
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "thresholds_frozen": False,
    "threshold_search_allowed_only_inside_this_inspected_corpus": True,
    "fresh_blind_data_must_not_be_loaded": True,
    "fixed_time_exits_before_stop_target_optimization": True,
    "costs_applied_in_strategy_evaluation_not_event_construction": True,
    "b2_target_sizing_restart": False,
    "strategy_d_remains_paused": True,
}

PROTOCOL = {
    "protocol_version": PROTOCOL_VERSION,
    "corpus_role": CORPUS_ROLE,
    "cohorts": COHORTS,
    "entry": ENTRY,
    "fixed_exit_horizons_minutes": FIXED_EXIT_HORIZONS_MINUTES,
    "outcomes": OUTCOMES,
    "strategy_families": STRATEGY_FAMILIES,
    "feature_policy": FEATURE_POLICY,
    "development_rules": DEVELOPMENT_RULES,
}
