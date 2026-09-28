from services.historical.independent_cohort2_protocol import (
    BLOCKS,
    EXPECTED,
    FUTURES_EXPIRIES,
    OPTION_EXPIRIES,
    PROTOCOL,
    SESSION_DATES,
    futures_expiry_for_day,
    option_expiry_for_day,
)


def test_cohort2_is_exactly_72_sessions_in_six_equal_blocks():
    assert len(SESSION_DATES) == 72
    assert SESSION_DATES[0] == "2025-09-09"
    assert SESSION_DATES[-1] == "2025-12-23"
    assert len(BLOCKS) == 6
    assert all(len(block) == 12 for block in BLOCKS)
    assert [day for block in BLOCKS for day in block] == SESSION_DATES
    assert EXPECTED["five_minute_rows"] == 5400
    assert EXPECTED["dynamic_option_contract_rows"] == 97200


def test_cohort2_does_not_overlap_any_recorded_inspected_window():
    for day in SESSION_DATES:
        for start, end, _label in PROTOCOL["previously_inspected_windows"]:
            assert not (start <= day <= end)


def test_futures_roll_schedule_is_explicit():
    assert FUTURES_EXPIRIES == [
        "2025-09-30",
        "2025-10-28",
        "2025-11-25",
        "2025-12-30",
    ]
    assert futures_expiry_for_day("2025-09-30") == "2025-09-30"
    assert futures_expiry_for_day("2025-10-01") == "2025-10-28"
    assert futures_expiry_for_day("2025-10-28") == "2025-10-28"
    assert futures_expiry_for_day("2025-10-29") == "2025-11-25"
    assert futures_expiry_for_day("2025-11-26") == "2025-12-30"


def test_options_schedule_includes_special_session_expiry_but_not_as_research_session():
    assert OPTION_EXPIRIES[0] == "2025-09-09"
    assert OPTION_EXPIRIES[-1] == "2025-12-23"
    assert "2025-10-21" in OPTION_EXPIRIES
    assert "2025-10-21" not in SESSION_DATES
    assert option_expiry_for_day("2025-10-20") == "2025-10-21"
    assert option_expiry_for_day("2025-10-23") == "2025-10-28"


def test_replication_protocol_forbids_discovery_rescue():
    rules = PROTOCOL["replication_rules"]
    assert PROTOCOL["protocol_version"] == "DEVELOPMENT_COHORT_2_V1_1"\n    assert PROTOCOL["protocol_correction"]["incorrect_value"] == "2025-10-21"\n    assert PROTOCOL["protocol_correction"]["correct_value"] == "2025-10-20"\n    assert PROTOCOL["candidate_frozen"] is False
    assert PROTOCOL["blind_data_used"] is False
    assert PROTOCOL["implementation_allowed"] is False
    assert rules["candidate_freeze"] is False
    assert rules["blind_validation"] is False
    assert rules["pnl_optimization"] is False
    assert rules["threshold_optimization"] is False
    assert "Do not rescue" in rules["failure_rule"]
