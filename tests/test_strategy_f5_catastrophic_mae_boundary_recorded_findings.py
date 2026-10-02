from services.historical.strategy_f5_catastrophic_mae_boundary_recorded_findings import (
    STRATEGY_F5_CATASTROPHIC_MAE_BOUNDARY_RECORDED_FINDINGS_V1,
)


def test_record_is_bound_to_corrected_uploaded_artifact():
    record = STRATEGY_F5_CATASTROPHIC_MAE_BOUNDARY_RECORDED_FINDINGS_V1
    assert record["source"]["artifact_sha256"] == (
        "6052a6c0d47da87362c80354309cabb5b3b081c832e54f327546f477878c7a75"
    )
    assert record["source"]["target_bearish_PE_trades"] == 84


def test_single_frozen_candidate_is_27_95():
    record = STRATEGY_F5_CATASTROPHIC_MAE_BOUNDARY_RECORDED_FINDINGS_V1
    candidate = record["derived_candidate"]
    assert candidate["stop_distance_pct"] == 27.95
    assert candidate["winner_preservation_pct"] >= 95.0
    assert candidate["activation_preservation_pct"] >= 95.0
    assert candidate["not_pnl_optimized"] is True


def test_30_60_is_reference_only():
    record = STRATEGY_F5_CATASTROPHIC_MAE_BOUNDARY_RECORDED_FINDINGS_V1
    assert record["reference_only"]["is_candidate"] is False
    assert record["guardrails"]["reference_30_60_not_a_candidate"] is True
    assert record["guardrails"]["no_stop_grid_search"] is True
