from services.historical.strategy_f5_catastrophic_stop_march_recorded_findings import (
    STRATEGY_F5_CATASTROPHIC_STOP_MARCH_RECORDED_FINDINGS_V1,
)


def test_march_record_is_bound_to_uploaded_artifact():
    record = STRATEGY_F5_CATASTROPHIC_STOP_MARCH_RECORDED_FINDINGS_V1
    assert record["source"]["artifact_sha256"] == (
        "35a0617b65d4ea33a20f96b53bf4b9a7c3c8e54700031a0a11897b15ad4a12c2"
    )
    assert record["source"]["target_bearish_PE_trades"] == 37


def test_march_is_inconclusive_not_failure():
    record = STRATEGY_F5_CATASTROPHIC_STOP_MARCH_RECORDED_FINDINGS_V1
    assert record["validation_gate"]["status"] == "INCONCLUSIVE_COVERAGE"
    assert record["validation_gate"]["observed_stop_triggers"] == 1
    assert record["guardrails"]["march_is_inconclusive_not_pass_or_fail"] is True


def test_stop_remains_frozen_after_sparse_march():
    record = STRATEGY_F5_CATASTROPHIC_STOP_MARCH_RECORDED_FINDINGS_V1
    assert record["frozen_candidate"]["stop_distance_pct"] == 27.95
    assert record["guardrails"]["keep_stop_27_95_frozen"] is True
    assert record["guardrails"]["no_stop_retuning"] is True
