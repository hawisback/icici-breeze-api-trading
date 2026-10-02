from services.historical.independent_ultra_early_range_forecast_recorded_findings import (
    ULTRA_EARLY_RANGE_FORECAST_RECORDED_FINDINGS_V1,
)


def test_ultra_early_record_is_sha_bound():
    record = ULTRA_EARLY_RANGE_FORECAST_RECORDED_FINDINGS_V1
    assert record["source"]["findings_artifact_sha256"] == (
        "c57c94b9aceb984bbd6664bac5d0daaee7fbf5b7ace7777445e59954963b1ea5"
    )
    assert record["source"]["training_market_sha256"] == (
        "dbc368f444d4d4956672b78b2d9e806f0940f526b4a013023e96d22592948ed5"
    )
    assert record["source"]["transfer_database_sha256"] == (
        "3e75de732077aa056c4027ed20f789059f11787fc7102c2de9a6a6ce04cbeee6"
    )


def test_both_ultra_early_checkpoints_pass_and_5m_is_earliest():
    record = ULTRA_EARLY_RANGE_FORECAST_RECORDED_FINDINGS_V1
    assert record["checkpoints_minutes"] == [5, 10]
    assert record["stable_checkpoints_minutes"] == [5, 10]
    assert record["earliest_stable_checkpoint_minutes"] == 5
    assert record["checkpoint_summary"]["5"]["gate_passed"] is True
    assert record["checkpoint_summary"]["10"]["gate_passed"] is True
    assert record["checkpoint_summary"]["5"]["bootstrap_95pct"][0] > 0.0
    assert record["checkpoint_summary"]["10"]["bootstrap_95pct"][0] > 0.0
    assert record["decision"] == "EARLIEST_STABLE_ULTRA_EARLY_RANGE_FORECAST_5M"


def test_ultra_early_record_closes_earlier_timing_at_5m_resolution():
    text = ULTRA_EARLY_RANGE_FORECAST_RECORDED_FINDINGS_V1["interpretation"]
    assert "earliest observable checkpoint" in text
    assert "no earlier" in text.lower()


def test_ultra_early_record_remains_nontrading():
    guardrails = ULTRA_EARLY_RANGE_FORECAST_RECORDED_FINDINGS_V1["guardrails"]
    assert guardrails["blind_validation"] is False
    assert guardrails["transfer_refit"] is False
    assert guardrails["feature_search"] is False
    assert guardrails["model_family_search"] is False
    assert guardrails["threshold_optimization"] is False
    assert guardrails["directional_entry_exit_rule"] is False
    assert guardrails["pnl_scored"] is False
    assert guardrails["implementation_allowed"] is False
    assert guardrails["no_rescue_on_same_samples"] is True
    assert guardrails["strategy_d_remains_paused"] is True
