"""Recorded pass of the frozen Development Cohort 3 collection audit."""

COHORT3_COLLECTION_AUDIT_RECORDED_V1 = {
    "research_type": "NIFTY_DEVELOPMENT_COHORT_3_COLLECTION_AUDIT_RECORDED_V1",
    "protocol_version": "DEVELOPMENT_COHORT_3_V1",
    "cohort_role": "NEW_INSPECTED_DEVELOPMENT_NOT_BLIND_VALIDATION",
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "audit_artifact_sha256": (
        "ee6e0bf50fb6e839e0f7b0fed98447bf03b73323e351d2e0dd7f43a6d62291ac"
    ),
    "expected": {
        "sessions": 80,
        "five_minute_rows": 6000,
        "one_minute_rows": 30000,
        "five_minute_bars_per_session": 75,
        "one_minute_bars_per_session": 375,
        "block_size_sessions": 10,
        "blocks": 8,
    },
    "validation": "PASSED",
    "artifacts": {
        "market": "PASSED",
        "vix": "PASSED",
        "options": "PASSED",
        "spot": "PASSED",
        "intrabar": "PASSED",
    },
    "decision": "FREEZE_RAW_SOURCE_HASHES_BEFORE_EVENT_CORPUS_BUILD",
    "guardrails": {
        "not_blind_validation": True,
        "no_candidate_promotion": True,
        "no_strategy_scoring_in_manifest_step": True,
        "strategy_d_remains_paused": True,
    },
}
