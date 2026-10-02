from services.historical.independent_cohort4_protocol import (
    COHORT_ROLE,
    EXPECTED,
    FUTURES_MONTHLY_EXPIRIES,
    GUARDRAILS,
    OPTION_EXPIRIES,
    SESSION_DATES,
)


def test_cohort4_is_new_development_not_blind():
    assert COHORT_ROLE == "NEW_INSPECTED_DEVELOPMENT_NOT_BLIND_VALIDATION"
    assert GUARDRAILS["research_only"] is True
    assert GUARDRAILS["candidate_frozen"] is False
    assert GUARDRAILS["blind_data_used"] is False
    assert GUARDRAILS["implementation_allowed"] is False
    assert GUARDRAILS["dates_frozen_before_collection"] is True
    assert GUARDRAILS["no_candidate_promotion_from_cohort4_alone"] is True
    assert GUARDRAILS["strategy_d_remains_paused"] is True


def test_cohort4_has_exactly_80_sessions_and_no_cohort2_overlap():
    assert len(SESSION_DATES) == 80
    assert len(set(SESSION_DATES)) == 80
    assert SESSION_DATES[0] == "2025-05-16"
    assert SESSION_DATES[-1] == "2025-09-08"
    assert all(day < "2025-09-09" for day in SESSION_DATES)


def test_in_range_2025_fo_holidays_are_excluded():
    assert "2025-08-15" not in SESSION_DATES
    assert "2025-08-27" not in SESSION_DATES
    # 05-Sep was an F&O settlement holiday, not an F&O trading holiday.
    assert "2025-09-05" in SESSION_DATES


def test_frozen_futures_expiry_schedule_handles_thursday_to_tuesday_transition():
    assert FUTURES_MONTHLY_EXPIRIES == [
        "2025-05-29",
        "2025-06-26",
        "2025-07-31",
        "2025-08-28",
        "2025-09-30",
    ]


def test_frozen_option_expiry_schedule_handles_transition():
    assert OPTION_EXPIRIES[:2] == ["2025-05-22", "2025-05-29"]
    assert "2025-08-28" in OPTION_EXPIRIES
    assert "2025-09-02" in OPTION_EXPIRIES
    assert OPTION_EXPIRIES[-1] == "2025-09-09"
    assert "2025-09-04" not in OPTION_EXPIRIES


def test_expected_shape_is_frozen():
    assert EXPECTED == {
        "sessions": 80,
        "five_minute_rows": 6000,
        "one_minute_rows": 30000,
        "five_minute_bars_per_session": 75,
        "one_minute_bars_per_session": 375,
        "block_size_sessions": 10,
        "blocks": 8,
    }
