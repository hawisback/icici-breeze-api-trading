from services.historical.independent_cohort3_protocol import (
    COHORT_ROLE,
    EXPECTED,
    FUTURES_MONTHLY_EXPIRIES,
    GUARDRAILS,
    OPTION_EXPIRIES,
    SESSION_DATES,
)


def test_cohort3_is_new_development_not_blind():
    assert COHORT_ROLE == "NEW_INSPECTED_DEVELOPMENT_NOT_BLIND_VALIDATION"
    assert GUARDRAILS["research_only"] is True
    assert GUARDRAILS["candidate_frozen"] is False
    assert GUARDRAILS["blind_data_used"] is False
    assert GUARDRAILS["implementation_allowed"] is False
    assert GUARDRAILS["dates_frozen_before_collection"] is True
    assert GUARDRAILS["no_candidate_promotion_from_cohort3_alone"] is True
    assert GUARDRAILS["strategy_d_remains_paused"] is True


def test_cohort3_has_exactly_80_nonoverlapping_sessions():
    assert len(SESSION_DATES) == 80
    assert len(set(SESSION_DATES)) == 80
    assert SESSION_DATES[0] == "2026-01-02"
    assert SESSION_DATES[-1] == "2026-05-05"
    assert all(day < "2026-05-19" for day in SESSION_DATES)
    assert all(not ("2025-09-09" <= day <= "2025-12-23") for day in SESSION_DATES)


def test_known_2026_fo_holidays_are_excluded():
    for holiday in (
        "2026-01-15",
        "2026-01-26",
        "2026-03-03",
        "2026-03-26",
        "2026-03-31",
        "2026-04-03",
        "2026-04-14",
        "2026-05-01",
    ):
        assert holiday not in SESSION_DATES


def test_frozen_futures_expiry_schedule_handles_march_holiday():
    assert FUTURES_MONTHLY_EXPIRIES == [
        "2026-01-27",
        "2026-02-24",
        "2026-03-30",
        "2026-04-28",
        "2026-05-26",
    ]


def test_frozen_option_expiry_schedule_handles_holiday_tuesdays():
    assert "2026-03-02" in OPTION_EXPIRIES
    assert "2026-03-03" not in OPTION_EXPIRIES
    assert "2026-03-30" in OPTION_EXPIRIES
    assert "2026-03-31" not in OPTION_EXPIRIES
    assert "2026-04-13" in OPTION_EXPIRIES
    assert "2026-04-14" not in OPTION_EXPIRIES
    assert OPTION_EXPIRIES[-1] == "2026-05-05"


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
