from services.historical.independent_opening_range_temporal_transfer_recorded_findings import (
    OPENING_RANGE_TEMPORAL_TRANSFER_RECORDED_FINDINGS_V1,
)


def test_temporal_transfer_record_is_sha_bound():
    record = OPENING_RANGE_TEMPORAL_TRANSFER_RECORDED_FINDINGS_V1
    assert record["source"]["findings_artifact_sha256"] == (
        "149534c08739ca883050ac8a7b310d4caf2d8ae6a8d840810bfb12b5af0829da"
    )
    assert record["source"]["training_market_sha256"] == (
        "dbc368f444d4d4956672b78b2d9e806f0940f526b4a013023e96d22592948ed5"
    )
    assert record["source"]["transfer_database_sha256"] == (
        "3e75de732077aa056c4027ed20f789059f11787fc7102c2de9a6a6ce04cbeee6"
    )
    assert record["source"]["training_sessions"] == 709
    assert record["source"]["transfer_complete_sessions"] == 412


def test_fixed_older_model_transferred_without_refit():
    record = OPENING_RANGE_TEMPORAL_TRANSFER_RECORDED_FINDINGS_V1
    assert record["fixed_model"]["refit_on_transfer_data"] is False
    assert record["fixed_baseline"]["refit_on_transfer_data"] is False
    assert record["calendar_year_results"]["2025"]["model_mae_lower"] is True
    assert record["calendar_year_results"]["2026"]["model_mae_lower"] is True
    assert record["calendar_year_results"]["2025"]["model_spearman"] > 0.0
    assert record["calendar_year_results"]["2026"]["model_spearman"] > 0.0
    assert record["pooled_transfer"]["bootstrap_error_improvement_95pct"][0] > 0.0
    assert record["transfer_gate"] == {"passed": True, "failures": []}
    assert record["decision"] == (
        "FIXED_OLDER_OPENING_RANGE_MODEL_TRANSFERS_TO_2025_2026"
    )


def test_temporal_transfer_record_remains_nontrading():
    guardrails = OPENING_RANGE_TEMPORAL_TRANSFER_RECORDED_FINDINGS_V1[
        "guardrails"
    ]
    assert guardrails["blind_validation"] is False
    assert guardrails["transfer_refit"] is False
    assert guardrails["feature_search"] is False
    assert guardrails["model_family_search"] is False
    assert guardrails["threshold_optimization"] is False
    assert guardrails["directional_entry_exit_rule"] is False
    assert guardrails["pnl_scored"] is False
    assert guardrails["implementation_allowed"] is False
    assert guardrails["no_rescue_on_same_transfer_sample"] is True
    assert guardrails["strategy_d_remains_paused"] is True
