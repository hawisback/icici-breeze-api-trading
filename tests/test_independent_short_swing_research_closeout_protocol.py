from services.historical.independent_short_swing_research_closeout_protocol import (
    CLOSEOUT_VERSION,
    FINAL_QA_STEP,
    GUARDRAILS,
    TERMINAL_DECISION_RULE,
)


def test_closeout_is_terminal_after_one_cohort4_qa_attempt():
    assert CLOSEOUT_VERSION == "INDEPENDENT_SHORT_SWING_RESEARCH_CLOSEOUT_V1"
    assert FINAL_QA_STEP["provider"] == "BREEZE"
    assert FINAL_QA_STEP["one_attempt_only"] is True
    assert FINAL_QA_STEP["strategy_outcomes_used"] is False
    assert TERMINAL_DECISION_RULE["additional_hypothesis_generation_allowed"] is False
    assert TERMINAL_DECISION_RULE["additional_cohort4_data_collection_allowed"] is False


def test_closeout_has_no_candidate_or_blind_promotion():
    assert TERMINAL_DECISION_RULE["candidate_frozen"] is False
    assert TERMINAL_DECISION_RULE["blind_validation_allowed"] is False
    assert TERMINAL_DECISION_RULE["implementation_allowed"] is False
    assert TERMINAL_DECISION_RULE["final_research_status"] == "NO_CANDIDATE_FREEZE"


def test_closeout_preserves_research_guardrails():
    assert GUARDRAILS["no_retest_of_rejected_studies"] is True
    assert GUARDRAILS["no_post_hoc_rescue"] is True
    assert GUARDRAILS["no_new_broker_or_trading_api_without_explicit_user_consent"] is True
    assert GUARDRAILS["strategy_d_remains_paused"] is True
