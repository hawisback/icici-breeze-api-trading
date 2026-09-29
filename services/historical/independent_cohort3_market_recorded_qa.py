"""Recorded QA for Development Cohort 3 market artifact.

This records the first collected Cohort-3 artifact after the exact-date protocol
was frozen. It is inspected development data, not blind validation.
"""

COHORT3_MARKET_RECORDED_QA_V1 = {
    "research_type": "NIFTY_DEVELOPMENT_COHORT_3_MARKET_RECORDED_QA_V1",
    "protocol_version": "DEVELOPMENT_COHORT_3_V1",
    "cohort_role": "NEW_INSPECTED_DEVELOPMENT_NOT_BLIND_VALIDATION",
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "artifact_sha256": (
        "f320af82ed96cf80b740afb3fe150b56d9a97213b180e5472169ba82a9353049"
    ),
    "artifact": {
        "sessions": 80,
        "five_minute_rows": 6000,
        "unique_timestamps": 6000,
        "duplicate_timestamps": 0,
        "bars_per_session": 75,
        "session_first_bar": "09:15",
        "session_last_bar": "15:25",
        "five_minute_spacing_exact": True,
        "missing_futures_ohlcv_oi_rows": 0,
        "invalid_ohlc_rows": 0,
        "nonpositive_volume_rows": 0,
        "nonpositive_open_interest_rows": 0,
        "wrong_contract_rows": 0,
        "canonical_futures_source": "BREEZE",
        "volume_semantics": "actual futures traded volume",
        "open_interest_semantics": "provider-reported futures open interest",
    },
    "roll_transitions": [
        {
            "first_session_new_contract": "2026-01-28",
            "from": "NIFTY FUT 2026-01-27",
            "to": "NIFTY FUT 2026-02-24",
        },
        {
            "first_session_new_contract": "2026-02-25",
            "from": "NIFTY FUT 2026-02-24",
            "to": "NIFTY FUT 2026-03-30",
        },
        {
            "first_session_new_contract": "2026-04-01",
            "from": "NIFTY FUT 2026-03-30",
            "to": "NIFTY FUT 2026-04-28",
        },
        {
            "first_session_new_contract": "2026-04-29",
            "from": "NIFTY FUT 2026-04-28",
            "to": "NIFTY FUT 2026-05-26",
        },
    ],
    "decision": "MARKET_ARTIFACT_QA_PASSED_CONTINUE_COHORT3_COLLECTION",
    "guardrails": {
        "not_blind_validation": True,
        "no_candidate_promotion": True,
        "do_not_substitute_niftybees_volume": True,
        "strategy_d_remains_paused": True,
    },
}
