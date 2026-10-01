from services.historical.independent_opening_range_forecast_utility_recorded_findings import (
    OPENING_RANGE_FORECAST_UTILITY_RECORDED_FINDINGS_V1,
)


def test_opening_range_utility_record_is_sha_bound():
    record = OPENING_RANGE_FORECAST_UTILITY_RECORDED_FINDINGS_V1
    assert record["source"]["findings_artifact_sha256"] == (
        "255253ead81149626cf8773bba6d9942fa70f96f9b62818f0829f8b2348405f8"
    )
    assert record["source"]["source_market_sha256"] == (
        "dbc368f444d4d4956672b78b2d9e806f0940f526b4a013023e96d22592948ed5"
    )
    assert record["source"]["source_complete_sessions"] == 709


def test_opening_range_utility_passed_both_folds_and_bootstrap():
    record = OPENING_RANGE_FORECAST_UTILITY_RECORDED_FINDINGS_V1
    assert all(row["model_mae_lower"] for row in record["folds"])
    assert record["pooled_oos"]["skill"] == 0.1921534260851273
    assert record["pooled_oos"]["mae_improvement_bps"] == 7.0225025124541
    assert record["pooled_oos"]["bootstrap_error_improvement_95pct"][0] > 0.0
    assert record["utility_gate"] == {"passed": True, "failures": []}
    assert record["decision"] == (
        "OPENING_RANGE_FORECAST_BEATS_UNCONDITIONAL_MEDIAN_BASELINE"
    )


def test_opening_range_utility_record_remains_nontrading():
    guardrails = OPENING_RANGE_FORECAST_UTILITY_RECORDED_FINDINGS_V1[
        "guardrails"
    ]
    assert guardrails["blind_validation"] is False
    assert guardrails["feature_search"] is False
    assert guardrails["model_family_search"] is False
    assert guardrails["threshold_optimization"] is False
    assert guardrails["directional_entry_exit_rule"] is False
    assert guardrails["pnl_scored"] is False
    assert guardrails["implementation_allowed"] is False
    assert guardrails["no_rescue_on_same_sample"] is True
    assert guardrails["strategy_d_remains_paused"] is True
