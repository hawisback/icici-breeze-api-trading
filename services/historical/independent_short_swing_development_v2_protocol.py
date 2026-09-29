"""Frozen protocol for the corrected 232-session short-swing development corpus.

This V2 corpus combines:
- the already-frozen 152-session V1 event artifact (Cohorts 1 and 2), and
- the newly audited 80-session Development Cohort 3 raw sources.

Cohorts 1 and 2 keep their frozen execution entries and fixed-horizon outcomes.
Their state features are recomputed from the OHLCV/OI, spot, VIX and options-gap
fields already stored in the frozen V1 event rows so that all three cohorts use
the same corrected full-window semantics.

No strategy signal, threshold, P&L, candidate selection, or blind validation is
performed by this protocol or its corpus builder.
"""
from __future__ import annotations

PROTOCOL_VERSION = "SHORT_SWING_DEVELOPMENT_V2"
CORPUS_ROLE = "INSPECTED_STRATEGY_DEVELOPMENT_NOT_VALIDATION"

FROZEN_INPUTS = {
    "cohort1_cohort2_event_v1": {
        "sha256": "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f",
        "sessions": 152,
        "five_minute_events": 11400,
    },
    "cohort3_source_manifest": {
        "sha256": "1d622797b0a97fba72da68bc78ba67d0b09beac525c1a074525764332591c166",
        "audit_sha256": (
            "ee6e0bf50fb6e839e0f7b0fed98447bf03b73323e351d2e0dd7f43a6d62291ac"
        ),
        "sources": {
            "market": "f320af82ed96cf80b740afb3fe150b56d9a97213b180e5472169ba82a9353049",
            "vix": "efd652dc50818832bb29dec86122f6bb8023187db1e25cd77c2f6c043662f9b5",
            "options": "103f75607a3ce72b9b5a2ec38308d6d707935acc0ecee8443932b6cd3bd283f2",
            "spot": "184a8ac426b5920ddbec3339ecf6975821bc912cbd61b2fc655a1156a1ffea26",
            "intrabar": "23e0e8d4e12824b5fa8bd2047c8686a5577b492e4b3f6c52f9ca0fb765cf546c",
        },
    },
}

COHORTS = {
    "cohort1": {
        "sessions": 80,
        "five_minute_rows": 6000,
        "one_minute_rows": 30000,
        "block_size": 10,
        "source": "frozen V1 event rows; execution outcomes preserved",
    },
    "cohort2": {
        "sessions": 72,
        "five_minute_rows": 5400,
        "one_minute_rows": 27000,
        "block_size": 12,
        "source": "frozen V1 event rows; execution outcomes preserved",
    },
    "cohort3": {
        "sessions": 80,
        "five_minute_rows": 6000,
        "one_minute_rows": 30000,
        "block_size": 10,
        "source": "frozen Cohort-3 raw sources and repaired one-minute futures",
    },
}

EXPECTED = {
    "sessions": 232,
    "five_minute_events": 17400,
    "chronological_blocks": 22,
    "scorable_events_by_horizon": {
        "5": 17168,
        "10": 16936,
        "15": 16704,
        "30": 16008,
    },
    "pure_window_eligible_rows": {
        "current_return": 17168,
        "range_3": 16936,
        "range_6": 16240,
        "prior_3": 16704,
        "path_length_3": 16704,
        "path_length_6": 16008,
    },
}

FEATURE_SEMANTICS_V2 = {
    "same_session_only": True,
    "full_declared_window_required": True,
    "partial_window_statistics_forbidden": True,
    "range_3": "current bar plus prior two bars; exactly three bars required",
    "range_6": "current bar plus prior five bars; exactly six bars required",
    "path_length_3": (
        "sum of three consecutive five-minute close-to-close returns; therefore "
        "four price bars are required"
    ),
    "path_length_6": (
        "sum of six consecutive five-minute close-to-close returns; therefore "
        "seven price bars are required"
    ),
    "prior_3_high_low_volume": (
        "exactly the prior three completed bars; all three must exist"
    ),
    "futures_return_and_oi_change": "same-session immediately prior bar only",
    "options_specific_fast_lead": (
        "recomputed separately inside each cohort with its frozen cohort blocks, "
        "using options_gap_bps residualized against recomputed spot_gap_bps and "
        "futures_return_bps"
    ),
    "v1_correction_reason": (
        "V1 pandas row-wise max/min/mean used skipna=True, so early-session "
        "3/6-bar and prior-3 features could be computed from partial windows. "
        "V2 explicitly forbids this."
    ),
}

OUTCOME_POLICY = {
    "cohort1_cohort2": (
        "preserve entry timestamps/prices and every fixed-horizon outcome from "
        "the hash-frozen V1 event artifact; do not recompute them"
    ),
    "cohort3": (
        "entry at the one-minute open immediately after the completed five-minute "
        "signal bar; fixed exits at 5/10/15/30 minutes from audited repaired "
        "one-minute futures; no stops/targets"
    ),
}

GUARDRAILS = {
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "strategy_scoring_in_builder": False,
    "threshold_search_in_builder": False,
    "no_fresh_blind_data": True,
    "no_retroactive_rescue_of_prior_hypotheses": True,
    "v2_does_not_relabel_prior_results": True,
    "prior_study_decisions_remain_recorded_as_run": True,
    "strategy_d_remains_paused": True,
}
