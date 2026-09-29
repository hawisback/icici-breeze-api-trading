from services.historical.independent_current_regime_magnitude_protocol import (
    BOOTSTRAP as PILOT_BOOTSTRAP,
    PREDICTOR as PILOT_PREDICTOR,
    SESSION_END_EXCLUSIVE as PILOT_SESSION_END_EXCLUSIVE,
    SESSION_START as PILOT_SESSION_START,
    TARGET as PILOT_TARGET,
)
from services.historical.independent_prospective_magnitude_protocol import (
    BLOCKS,
    BOOTSTRAP,
    COLLECTION_AND_SCORING_POLICY,
    CONTRACT_BY_DATE,
    EXCLUDED_FNO_HOLIDAYS_WITHIN_WINDOW,
    EXPECTED_BARS_PER_SESSION,
    EXPECTED_ROWS,
    EXPECTED_SCORABLE_EVENTS,
    EXPECTED_SCORABLE_EVENTS_PER_SESSION,
    FORBIDDEN_FEATURES,
    GUARDRAILS,
    NSE_VERIFICATION,
    PREDICTOR,
    PROTOCOL_VERSION,
    REPLICATION_GATE,
    ROLL_SCHEDULE,
    SESSION_DATES,
    SESSION_END_EXCLUSIVE,
    SESSION_START,
    TARGET,
    TERMINAL_OUTCOMES,
)


def test_prospective_window_is_exactly_30_forward_sessions():
    assert PROTOCOL_VERSION == "NIFTY_PROSPECTIVE_MAGNITUDE_REPLICATION_V1"
    assert len(SESSION_DATES) == 30
    assert SESSION_DATES[0] == "2026-10-01"
    assert SESSION_DATES[-1] == "2026-11-16"
    assert all(day > "2026-09-30" for day in SESSION_DATES)
    assert EXCLUDED_FNO_HOLIDAYS_WITHIN_WINDOW == [
        "2026-10-02",
        "2026-10-20",
        "2026-11-10",
    ]
    assert not set(EXCLUDED_FNO_HOLIDAYS_WITHIN_WINDOW) & set(SESSION_DATES)
    assert [len(BLOCKS[name]) for name in ("block1", "block2", "block3")] == [
        10,
        10,
        10,
    ]


def test_exact_roll_schedule_is_frozen_before_collection():
    assert ROLL_SCHEDULE == [
        {
            "first_session": "2026-10-01",
            "last_session": "2026-10-27",
            "futures_expiry": "2026-10-27",
        },
        {
            "first_session": "2026-10-28",
            "last_session": "2026-11-16",
            "futures_expiry": "2026-11-23",
        },
    ]
    assert len(CONTRACT_BY_DATE) == 30
    assert all(CONTRACT_BY_DATE[day] == "2026-10-27" for day in SESSION_DATES[:17])
    assert all(CONTRACT_BY_DATE[day] == "2026-11-23" for day in SESSION_DATES[17:])
    assert CONTRACT_BY_DATE["2026-10-27"] == "2026-10-27"
    assert CONTRACT_BY_DATE["2026-10-28"] == "2026-11-23"


def test_current_session_semantics_and_exact_sample_size_are_frozen():
    assert SESSION_START == PILOT_SESSION_START == "09:15"
    assert SESSION_END_EXCLUSIVE == PILOT_SESSION_END_EXCLUSIVE == "15:40"
    assert EXPECTED_BARS_PER_SESSION == 77
    assert EXPECTED_ROWS == 30 * 77 == 2310
    assert EXPECTED_SCORABLE_EVENTS_PER_SESSION == 66
    assert EXPECTED_SCORABLE_EVENTS == 30 * 66 == 1980
    assert REPLICATION_GATE["minimum_scorable_events"] == 1980


def test_predictor_target_and_bootstrap_are_unchanged_from_replication_pilot():
    assert PREDICTOR == PILOT_PREDICTOR
    assert TARGET == PILOT_TARGET
    assert BOOTSTRAP == PILOT_BOOTSTRAP
    assert PREDICTOR["bars"] == 6
    assert TARGET["future_bars"] == 6


def test_descriptive_gate_has_no_magnitude_threshold():
    assert REPLICATION_GATE == {
        "minimum_scorable_events": 1980,
        "pooled_spearman_must_be_positive": True,
        "all_three_block_spearman_must_be_positive": True,
        "session_cluster_bootstrap_95pct_lower_must_be_positive": True,
    }
    assert "magnitude_threshold" not in REPLICATION_GATE
    assert "quartile_gate" not in REPLICATION_GATE


def test_collection_cannot_score_future_or_partial_sample():
    policy = COLLECTION_AND_SCORING_POLICY
    assert policy["provider"] == "BREEZE"
    assert policy["session_may_be_collected_only_after_regular_session_completed"] is True
    assert policy["regular_session_completion_time_ist"] == "15:40"
    assert policy["partial_session_collection_for_scoring_forbidden"] is True
    assert policy["score_before_all_30_sessions_complete"] is False
    assert policy["full_sample_not_complete_before"] == "2026-11-16T15:40:00+05:30"
    assert policy["qa_only_before_full_sample_completion"] is True
    assert policy["no_outcome_inspection_before_full_sample_completion"] is True


def test_calendar_sources_and_expiry_basis_are_recorded():
    assert NSE_VERIFICATION["verified_on"] == "2026-09-30"
    assert "NSE/FAOP/71777" in NSE_VERIFICATION["fno_holiday_source"]
    assert "expiry rule" in NSE_VERIFICATION["contract_specification_source"]
    assert "Market Timings" in NSE_VERIFICATION["market_timing_source"]


def test_forbidden_features_and_terminal_decisions_prevent_rescue_or_promotion():
    assert FORBIDDEN_FEATURES == [
        "direction",
        "pnl",
        "quartile_thresholds",
        "range_cutoffs",
        "position_sizing",
        "options",
        "vix",
        "volume_filters",
        "oi_filters",
        "time_of_day_filters",
        "dte_filters",
        "other_features",
    ]
    assert TERMINAL_OUTCOMES["if_fail"] == (
        "RETIRE_MAGNITUDE_THESIS_NO_FURTHER_TUNING_OR_RESCUE"
    )
    assert "NO_TRADING_PROMOTION" in TERMINAL_OUTCOMES["if_pass"]
    assert GUARDRAILS["provider"] == "BREEZE"
    assert GUARDRAILS["new_broker_or_trading_api_used"] is False
    assert GUARDRAILS["directional_claim"] is False
    assert GUARDRAILS["pnl_scored"] is False
    assert GUARDRAILS["threshold_selection"] is False
    assert GUARDRAILS["position_sizing_rule"] is False
    assert GUARDRAILS["no_parameter_changes_after_freeze"] is True
    assert GUARDRAILS["no_rescue_after_results"] is True
    assert GUARDRAILS["pass_does_not_create_trading_candidate"] is True
    assert GUARDRAILS["pass_does_not_authorize_blind_validation"] is True
    assert GUARDRAILS["pass_does_not_authorize_implementation"] is True
    assert GUARDRAILS["strategy_d_remains_paused"] is True
