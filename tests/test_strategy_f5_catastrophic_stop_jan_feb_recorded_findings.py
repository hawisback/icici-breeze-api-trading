from services.historical.strategy_f5_catastrophic_stop_jan_feb_recorded_findings import (
    STRATEGY_F5_CATASTROPHIC_STOP_JAN_FEB_RECORDED_FINDINGS_V1,
)


def test_record_is_bound_to_uploaded_artifact():
    record = STRATEGY_F5_CATASTROPHIC_STOP_JAN_FEB_RECORDED_FINDINGS_V1
    assert record["source"]["artifact_sha256"] == (
        "8881532e0b97030d8cc92be202be6a6f6c4049f3be97fcbf18b13a0e52e43f17"
    )
    assert record["source"]["target_bearish_PE_trades"] == 104


def test_jan_feb_is_inconclusive_with_zero_triggers():
    record = STRATEGY_F5_CATASTROPHIC_STOP_JAN_FEB_RECORDED_FINDINGS_V1
    assert record["validation_gate"]["status"] == "INCONCLUSIVE_COVERAGE"
    assert record["validation_gate"]["observed_stop_triggers"] == 0
    assert record["guardrails"]["jan_feb_is_inconclusive_not_pass_or_fail"] is True


def test_combined_fresh_evidence_is_sparse():
    record = STRATEGY_F5_CATASTROPHIC_STOP_JAN_FEB_RECORDED_FINDINGS_V1
    fresh = record["fresh_validation_evidence"]
    assert fresh["combined_target_trades"] == 141
    assert fresh["combined_stop_triggers"] == 1
    assert record["guardrails"]["do_not_call_27_95_validated"] is True
