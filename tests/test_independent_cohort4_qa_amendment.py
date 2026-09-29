from services.historical.independent_cohort4_qa_amendment import (
    EXPECTED,
    GUARDRAILS,
    REMOVED_SESSION,
    REPLACEMENT_FUTURES_EXPIRY,
    REPLACEMENT_SESSION,
    SESSION_DATES,
    validate_amended_dates,
)


def test_amended_cohort4_keeps_80_sessions_without_bad_session():
    validate_amended_dates()
    assert len(SESSION_DATES) == 80
    assert len(set(SESSION_DATES)) == 80
    assert REMOVED_SESSION == "2025-06-27"
    assert REMOVED_SESSION not in SESSION_DATES
    assert REPLACEMENT_SESSION == "2025-05-15"
    assert REPLACEMENT_SESSION in SESSION_DATES
    assert REPLACEMENT_FUTURES_EXPIRY == "2025-05-29"
    assert SESSION_DATES[0] == "2025-05-15"
    assert SESSION_DATES[-1] == "2025-09-08"


def test_substitution_is_qa_only_and_breeze_only():
    assert GUARDRAILS["qa_only_substitution"] is True
    assert GUARDRAILS["strategy_outcomes_not_used_for_substitution"] is True
    assert GUARDRAILS["authorized_provider_for_replacement"] == "BREEZE"
    assert (
        GUARDRAILS[
            "new_external_broker_api_allowed_without_explicit_consent"
        ]
        is False
    )
    assert GUARDRAILS["no_abs_volume_transform"] is True
    assert GUARDRAILS["strategy_d_remains_paused"] is True


def test_expected_market_shape_unchanged():
    assert EXPECTED == {
        "sessions": 80,
        "five_minute_rows": 6000,
        "five_minute_bars_per_session": 75,
    }
