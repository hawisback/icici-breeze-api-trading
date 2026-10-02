from services.historical.strategy_f5_hist_strength_may_recorded_findings import (
    STRATEGY_F5_HIST_STRENGTH_MAY_RECORDED_FINDINGS_V1,
)


def test_may_hist_holdout_is_sha_bound():
    record = STRATEGY_F5_HIST_STRENGTH_MAY_RECORDED_FINDINGS_V1
    assert record["source"]["holdout_artifact_sha256"] == (
        "10fda06290b5a1f1f19cb6f37f1c349fc8b84e571bb67ca4a1a2e6e899b67ade"
    )


def test_histogram_filter_improved_selectivity_but_not_profitability():
    record = STRATEGY_F5_HIST_STRENGTH_MAY_RECORDED_FINDINGS_V1
    candidate = record["candidate"]
    baseline = record["baseline"]
    assert candidate["trail_activation_rate_pct"] > baseline["trail_activation_rate_pct"]
    assert candidate["max_drawdown_inr"] < baseline["max_drawdown_inr"]
    assert candidate["net_pnl_inr"] > baseline["net_pnl_inr"]
    assert candidate["net_pnl_inr"] < 0
    assert candidate["profit_factor"] < 1.0


def test_may_holdout_failed_without_retuning():
    record = STRATEGY_F5_HIST_STRENGTH_MAY_RECORDED_FINDINGS_V1
    assert record["validation_gate"]["passed"] is False
    assert record["decision"] == (
        "REJECT_STANDALONE_HISTOGRAM_STRENGTH_FILTER_NO_MAY_RETUNING"
    )
    assert record["guardrails"]["do_not_tune_histogram_threshold_on_may"] is True
    assert record["guardrails"]["do_not_promote_pe_only_from_may_posthoc_split"] is True
