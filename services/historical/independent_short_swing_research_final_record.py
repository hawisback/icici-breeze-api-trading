"""Terminal recorded conclusion for the independent NIFTY short-swing research.

The final Breeze-only Development Cohort 4 QA substitution passed. Cohort 4 is
therefore archived as clean but UNUSED inspected development evidence. No
additional Cohort-4 options, VIX, spot, or intrabar data are collected and no
strategy is scored on this amended cohort.

This file implements the pre-frozen terminal rule from
INDEPENDENT_SHORT_SWING_RESEARCH_CLOSEOUT_V1.

Final conclusion
----------------
No tested strategy satisfied its complete predeclared candidate gate. Therefore:
- no candidate is frozen;
- blind validation remains untouched and is not opened;
- implementation is not authorized;
- rejected studies are not rescued or retuned;
- no additional hypothesis is generated from the inspected development data;
- Strategy D remains paused.

The research is closed with status NO_CANDIDATE_FREEZE.
"""

FINAL_RESEARCH_RECORD_V1 = {
    "research_type": "NIFTY_INDEPENDENT_SHORT_SWING_RESEARCH_FINAL_RECORD_V1",
    "closeout_protocol": "INDEPENDENT_SHORT_SWING_RESEARCH_CLOSEOUT_V1",
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "final_research_status": "NO_CANDIDATE_FREEZE",
    "development_v2": {
        "sessions": 232,
        "five_minute_events": 17400,
        "sha256": (
            "e4c08b2bbf8488c9a5bd1fd6229b242f6ce9f0e27060c2cce16b03886724ab46"
        ),
        "role": "INSPECTED_STRATEGY_DEVELOPMENT_NOT_VALIDATION",
    },
    "cohort4_final_market_qa": {
        "role": "CLEAN_UNUSED_INSPECTED_DEVELOPMENT_EVIDENCE",
        "artifact_sha256": (
            "931a34789a14ae2224d7fee4bd0d90906f347cfdac335334ca1c8fb5de023410"
        ),
        "sessions": 80,
        "five_minute_rows": 6000,
        "first_session": "2025-05-15",
        "last_session": "2025-09-08",
        "removed_invalid_session": "2025-06-27",
        "replacement_session": "2025-05-15",
        "replacement_provider": "BREEZE",
        "duplicate_rows": 0,
        "invalid_ohlc_rows": 0,
        "missing_volume_rows": 0,
        "missing_open_interest_rows": 0,
        "nonpositive_volume_rows": 0,
        "nonpositive_open_interest_rows": 0,
        "wrong_contract_rows": 0,
        "complete_75_bar_sessions": 80,
        "strategy_outcomes_used_for_substitution": False,
        "strategy_scored_on_amended_cohort": False,
        "additional_cohort4_data_collection": False,
    },
    "post_v2_frozen_studies": [
        {
            "study": "signed_volume_price_divergence_catch_up",
            "findings_sha256": (
                "652639cb6122ea6935eaab95376c38c462d7dc8b95fe26a874abf1444ec8ea47"
            ),
            "decision": "REJECTED_NO_CANDIDATE_FREEZE",
        },
        {
            "study": "session_vwap_cross_through_continuation",
            "findings_sha256": (
                "b365858c45c361ed3e07ca35123476f43bcac211cc093ae197a8c1f873119b48"
            ),
            "decision": "REJECTED_NO_CANDIDATE_FREEZE",
        },
        {
            "study": "prior_session_late_day_momentum_spillover",
            "findings_sha256": (
                "0cd1253aafbefa57f4f086d77ddea6fefc7bb7af459af99d364c5edb1b381600"
            ),
            "decision": "REJECTED_NO_CANDIDATE_FREEZE",
        },
        {
            "study": "external_opening_to_closing_intraday_momentum",
            "findings_sha256": (
                "2d1f5633e4adb1d068ed26cc69d1a0c942b0d726f669264cc4a407051a1ca722"
            ),
            "decision": "REJECTED_NO_CANDIDATE_FREEZE",
        },
    ],
    "retained_descriptive_findings": {
        "recent_futures_range_predicts_future_movement_magnitude": (
            "descriptive development evidence only; no target sizing or live use"
        ),
        "options_fast_lead": (
            "replicated as earliest executable timing/filter information only; "
            "not a standalone strategy"
        ),
        "one_minute_timing": (
            "edge concentrated at earliest executable minute in development; "
            "waiting to minute 2 destroyed the observed effect"
        ),
    },
    "guardrails": {
        "no_candidate_freeze": True,
        "no_blind_validation": True,
        "no_implementation": True,
        "no_retest_of_rejected_studies": True,
        "no_post_hoc_rescue": True,
        "no_threshold_rescue": True,
        "no_filter_rescue": True,
        "no_direction_flip_rescue": True,
        "no_horizon_rescue": True,
        "no_additional_hypothesis_generation": True,
        "no_additional_cohort4_collection": True,
        "no_new_broker_or_trading_api_without_explicit_user_consent": True,
        "no_merge_performed": True,
        "strategy_d_remains_paused": True,
    },
}
