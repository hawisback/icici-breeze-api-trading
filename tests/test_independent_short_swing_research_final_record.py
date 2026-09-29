from services.historical.independent_short_swing_research_final_record import (
    FINAL_RESEARCH_RECORD_V1,
)


def test_final_research_status_has_no_candidate_or_blind_promotion():
    record = FINAL_RESEARCH_RECORD_V1
    assert record["final_research_status"] == "NO_CANDIDATE_FREEZE"
    assert record["candidate_frozen"] is False
    assert record["blind_data_used"] is False
    assert record["implementation_allowed"] is False


def test_final_cohort4_is_clean_unused_development_evidence():
    cohort4 = FINAL_RESEARCH_RECORD_V1["cohort4_final_market_qa"]
    assert cohort4["role"] == "CLEAN_UNUSED_INSPECTED_DEVELOPMENT_EVIDENCE"
    assert cohort4["artifact_sha256"] == (
        "931a34789a14ae2224d7fee4bd0d90906f347cfdac335334ca1c8fb5de023410"
    )
    assert cohort4["sessions"] == 80
    assert cohort4["five_minute_rows"] == 6000
    assert cohort4["nonpositive_volume_rows"] == 0
    assert cohort4["nonpositive_open_interest_rows"] == 0
    assert cohort4["wrong_contract_rows"] == 0
    assert cohort4["complete_75_bar_sessions"] == 80
    assert cohort4["strategy_scored_on_amended_cohort"] is False
    assert cohort4["additional_cohort4_data_collection"] is False


def test_final_record_closes_all_post_v2_studies():
    studies = FINAL_RESEARCH_RECORD_V1["post_v2_frozen_studies"]
    assert len(studies) == 4
    assert all(
        study["decision"] == "REJECTED_NO_CANDIDATE_FREEZE"
        for study in studies
    )


def test_final_guardrails_are_terminal():
    guardrails = FINAL_RESEARCH_RECORD_V1["guardrails"]
    assert guardrails["no_candidate_freeze"] is True
    assert guardrails["no_blind_validation"] is True
    assert guardrails["no_implementation"] is True
    assert guardrails["no_additional_hypothesis_generation"] is True
    assert guardrails["no_additional_cohort4_collection"] is True
    assert guardrails["no_merge_performed"] is True
    assert guardrails["strategy_d_remains_paused"] is True
