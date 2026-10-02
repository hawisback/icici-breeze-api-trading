"""Frozen closeout protocol for the independent NIFTY short-swing research.

This closeout rule is frozen BEFORE the final Cohort-4 QA substitution result is
inspected.

Terminal rule
-------------
After one Breeze-only QA substitution attempt for Development Cohort 4:
- if the amended market artifact passes strict QA, freeze/archive its hash as
  clean unused inspected development evidence;
- if it fails strict QA, record Cohort 4 as unusable and abandon it;
- in either case, do not formulate or test another hypothesis on the inspected
  development data;
- do not collect additional options/VIX/spot/intrabar data for Cohort 4;
- do not load or collect fresh blind-validation data;
- do not promote any rejected study;
- do not authorize implementation.

Reason
------
The research has already exhausted multiple distinct, predeclared mechanisms
on the original 152-session development corpus and the corrected 232-session V2
corpus. Three new endogenous V2 studies and one externally motivated literature
study all failed their frozen gates. Continuing to generate rules after these
failures would increase hypothesis-mining risk without a genuine candidate.

The final research state is therefore NO_CANDIDATE_FREEZE unless a previously
frozen protocol already passed its full gate. None did.
"""

CLOSEOUT_VERSION = "INDEPENDENT_SHORT_SWING_RESEARCH_CLOSEOUT_V1"

FROZEN_REFERENCES = {
    "development_v2_sha256": (
        "e4c08b2bbf8488c9a5bd1fd6229b242f6ce9f0e27060c2cce16b03886724ab46"
    ),
    "signed_volume_price_divergence_findings_sha256": (
        "652639cb6122ea6935eaab95376c38c462d7dc8b95fe26a874abf1444ec8ea47"
    ),
    "session_vwap_cross_findings_sha256": (
        "b365858c45c361ed3e07ca35123476f43bcac211cc093ae197a8c1f873119b48"
    ),
    "late_day_spillover_findings_sha256": (
        "0cd1253aafbefa57f4f086d77ddea6fefc7bb7af459af99d364c5edb1b381600"
    ),
    "external_open_close_momentum_findings_sha256": (
        "2d1f5633e4adb1d068ed26cc69d1a0c942b0d726f669264cc4a407051a1ca722"
    ),
    "cohort4_failed_repair_sha256": (
        "e87ae99cd56d26e3ddd39469f9cda32cfa7efc60ac9802c3e62d99a8d11a9803"
    ),
}

FINAL_QA_STEP = {
    "name": "DEVELOPMENT_COHORT_4_QA_SUBSTITUTION_V1",
    "provider": "BREEZE",
    "removed_session": "2025-06-27",
    "replacement_session": "2025-05-15",
    "one_attempt_only": True,
    "strategy_outcomes_used": False,
}

TERMINAL_DECISION_RULE = {
    "if_cohort4_qa_passes": (
        "archive clean Cohort 4 market artifact as unused inspected development; "
        "do not collect more Cohort-4 components; close research"
    ),
    "if_cohort4_qa_fails": (
        "record Cohort 4 unusable; abandon Cohort 4; close research"
    ),
    "candidate_frozen": False,
    "blind_validation_allowed": False,
    "implementation_allowed": False,
    "additional_hypothesis_generation_allowed": False,
    "additional_cohort4_data_collection_allowed": False,
    "final_research_status": "NO_CANDIDATE_FREEZE",
}

GUARDRAILS = {
    "research_only": True,
    "blind_data_used": False,
    "no_merge": True,
    "no_retest_of_rejected_studies": True,
    "no_post_hoc_rescue": True,
    "no_threshold_rescue": True,
    "no_filter_rescue": True,
    "no_direction_flip_rescue": True,
    "no_horizon_rescue": True,
    "no_new_broker_or_trading_api_without_explicit_user_consent": True,
    "strategy_d_remains_paused": True,
}
