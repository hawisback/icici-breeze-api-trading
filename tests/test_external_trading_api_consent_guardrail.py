from services.historical.external_trading_api_consent_guardrail import POLICY


def test_new_trading_provider_requires_explicit_user_consent():
    assert POLICY["new_broker_or_trading_api_requires_explicit_user_consent"] is True
    assert POLICY["no_implicit_provider_substitution"] is True
    assert POLICY["no_account_signup_request_without_user_request"] is True


def test_cohort4_recovery_is_breeze_only_until_user_authorizes_other_provider():
    assert POLICY["current_cohort4_qa_authorized_providers"] == ["BREEZE"]
    assert "UPSTOX" in POLICY["current_cohort4_qa_unapproved_providers"]
    assert "DHAN" in POLICY["current_cohort4_qa_unapproved_providers"]
    assert "KITE" in POLICY["current_cohort4_qa_unapproved_providers"]
