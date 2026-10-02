"""Recorded QA for the corrected 232-session short-swing development corpus.

The V2 corpus corrects the V1 partial-window feature construction while preserving
the hash-frozen Cohort-1/2 execution outcomes. It adds audited Development Cohort
3 and remains inspected development data, not blind validation.

This record does not reopen, rerun, rescue, or relabel any prior study decision.
"""

SHORT_SWING_DEVELOPMENT_V2_RECORDED_QA = {
    "research_type": "NIFTY_SHORT_SWING_DEVELOPMENT_V2_RECORDED_QA",
    "protocol_version": "SHORT_SWING_DEVELOPMENT_V2",
    "corpus_role": "INSPECTED_STRATEGY_DEVELOPMENT_NOT_VALIDATION",
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "artifact_sha256": (
        "e4c08b2bbf8488c9a5bd1fd6229b242f6ce9f0e27060c2cce16b03886724ab46"
    ),
    "artifact": {
        "sessions": 232,
        "five_minute_events": 17400,
        "cohort_rows": {
            "cohort1": 6000,
            "cohort2": 5400,
            "cohort3": 6000,
        },
        "chronological_blocks": 22,
        "scorable_events_by_horizon": {
            "5": 17168,
            "10": 16936,
            "15": 16704,
            "30": 16008,
        },
        "feature_window_counts": {
            "current_return": 17168,
            "range_3": 16936,
            "range_6": 16240,
            "prior_3": 16704,
            "path_length_3": 16704,
            "path_length_6": 16008,
        },
        "cohort_date_ranges": {
            "cohort1": ["2026-05-19", "2026-09-09"],
            "cohort2": ["2025-09-09", "2025-12-23"],
            "cohort3": ["2026-01-02", "2026-05-05"],
        },
        "sessions_per_block": {
            "cohort1": 10,
            "cohort2": 12,
            "cohort3": 10,
        },
        "unique_event_ids": 17400,
        "event_ids_strictly_chronological": True,
        "unique_timestamps": 17400,
        "five_minute_session_spacing_exact": True,
        "session_first_bar": "09:15",
        "session_last_bar": "15:25",
        "legacy_v1_execution_outcomes_preserved": True,
        "cohort3_intrabar_rows": 30000,
        "cohort3_five_minute_rows_reconciled": 6000,
        "cohort3_exact_ohlcv_open_interest_reconciliation": True,
        "cohort3_exact_contract_reconciliation": True,
    },
    "independent_feature_recalculation": {
        "same_session_full_window_null_patterns_exact": True,
        "futures_return_bps": "MATCH",
        "oi_change_bps": "MATCH",
        "range_3_bps": "MATCH",
        "range_6_bps": "MATCH",
        "close_location_3": "MATCH",
        "close_location_6": "MATCH",
        "net_return_3_bps": "MATCH",
        "net_return_6_bps": "MATCH",
        "path_length_3_bps": "MATCH",
        "path_length_6_bps": "MATCH",
        "prior_3_high": "MATCH",
        "prior_3_low": "MATCH",
        "volume_vs_prior3_mean": "MATCH",
        "vix_5m_change_bps": "MATCH",
        "vix_15m_change_bps": "MATCH",
        "breakout_magnitudes": "MATCH",
        "breakout_boolean_states": "MATCH",
        "max_numeric_difference_bps_or_native_units": 5.01e-11,
    },
    "source_hashes": {
        "legacy_v1_events": (
            "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f"
        ),
        "cohort3_manifest": (
            "1d622797b0a97fba72da68bc78ba67d0b09beac525c1a074525764332591c166"
        ),
        "cohort3_market": (
            "f320af82ed96cf80b740afb3fe150b56d9a97213b180e5472169ba82a9353049"
        ),
        "cohort3_vix": (
            "efd652dc50818832bb29dec86122f6bb8023187db1e25cd77c2f6c043662f9b5"
        ),
        "cohort3_options": (
            "103f75607a3ce72b9b5a2ec38308d6d707935acc0ecee8443932b6cd3bd283f2"
        ),
        "cohort3_spot": (
            "184a8ac426b5920ddbec3339ecf6975821bc912cbd61b2fc655a1156a1ffea26"
        ),
        "cohort3_intrabar": (
            "23e0e8d4e12824b5fa8bd2047c8686a5577b492e4b3f6c52f9ca0fb765cf546c"
        ),
    },
    "decision": "V2_DEVELOPMENT_CORPUS_QA_PASSED_AND_HASH_FROZEN",
    "guardrails": {
        "future_development_protocols_must_pin_v2_sha": True,
        "no_prior_study_retest_or_rescue": True,
        "prior_study_decisions_remain_as_run": True,
        "no_blind_validation": True,
        "no_candidate_freeze_from_corpus_build": True,
        "strategy_d_remains_paused": True,
    },
}
