from services.historical.independent_range_forecast_checkpoints_recorded_findings import (
    RANGE_FORECAST_CHECKPOINTS_RECORDED_FINDINGS_V1,
)


def test_checkpoint_record_is_sha_bound():
    record = RANGE_FORECAST_CHECKPOINTS_RECORDED_FINDINGS_V1
    assert record["source"]["findings_artifact_sha256"] == (
        "23db0ef4eb21d92f497e88bcf8784dcb150e21eb88442b5b9a64478033cd2476"
    )
    assert record["source"]["training_market_sha256"] == (
        "dbc368f444d4d4956672b78b2d9e806f0940f526b4a013023e96d22592948ed5"
    )
    assert record["source"]["transfer_database_sha256"] == (
        "3e75de732077aa056c4027ed20f789059f11787fc7102c2de9a6a6ce04cbeee6"
    )


def test_all_frozen_checkpoints_pass_and_15m_is_earliest():
    record = RANGE_FORECAST_CHECKPOINTS_RECORDED_FINDINGS_V1
    assert record["checkpoints_minutes"] == [15, 30, 60, 90, 120]
    assert record["stable_checkpoints_minutes"] == [15, 30, 60, 90, 120]
    assert record["earliest_stable_checkpoint_minutes"] == 15
    assert all(
        row["gate_passed"] for row in record["checkpoint_summary"].values()
    )
    assert record["decision"] == "EARLIEST_STABLE_RANGE_FORECAST_CHECKPOINT_15M"


def test_checkpoint_record_remains_nontrading():
    guardrails = RANGE_FORECAST_CHECKPOINTS_RECORDED_FINDINGS_V1["guardrails"]
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
