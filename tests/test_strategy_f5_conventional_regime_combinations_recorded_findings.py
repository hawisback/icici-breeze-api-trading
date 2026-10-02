from services.historical.strategy_f5_conventional_regime_combinations_recorded_findings import (
    STRATEGY_F5_CONVENTIONAL_REGIME_COMBINATIONS_RECORDED_FINDINGS_V1,
)


def test_conventional_regime_findings_are_sha_bound():
    record = STRATEGY_F5_CONVENTIONAL_REGIME_COMBINATIONS_RECORDED_FINDINGS_V1
    assert record["source"]["artifact_sha256"] == (
        "bffe04030bcba47093fffc22c13734885ed0892842f40edd3409d63be264b2e6"
    )


def test_no_candidate_was_selected_posthoc():
    record = STRATEGY_F5_CONVENTIONAL_REGIME_COMBINATIONS_RECORDED_FINDINGS_V1
    assert record["decision"] == (
        "NO_CONVENTIONAL_REGIME_COMBINATION_PASSED_DEVELOPMENT_SCREEN"
    )
    assert all(
        candidate["passed"] is False
        for candidate in record["candidates"].values()
    )
    assert record["guardrails"]["no_candidate_selected_posthoc"] is True


def test_all_candidates_improved_pooled_pnl_but_failed_preservation():
    record = STRATEGY_F5_CONVENTIONAL_REGIME_COMBINATIONS_RECORDED_FINDINGS_V1
    for candidate in record["candidates"].values():
        assert candidate["net_delta_vs_baseline_inr"] > 0
        assert (
            candidate["baseline_activated_signal_capture_pct"] < 65.0
            or candidate["baseline_winner_signal_capture_pct"] < 65.0
        )
