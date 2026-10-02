from services.historical.strategy_f5_score_ge2_april_recorded_findings import (
    STRATEGY_F5_SCORE_GE2_APRIL_RECORDED_FINDINGS_V1,
)


def test_april_score_holdout_findings_are_sha_bound():
    record = STRATEGY_F5_SCORE_GE2_APRIL_RECORDED_FINDINGS_V1
    assert record["source"]["holdout_artifact_sha256"] == (
        "9862b569e8db9015f53a0c1e0f8c9ff96c878e40fa149313f561eb0e22b752ba"
    )


def test_selectivity_replicated_but_economics_failed():
    record = STRATEGY_F5_SCORE_GE2_APRIL_RECORDED_FINDINGS_V1
    candidate = record["candidate"]
    baseline = record["baseline"]
    assert candidate["trail_activation_rate_pct"] > baseline["trail_activation_rate_pct"]
    assert candidate["win_rate_pct"] > baseline["win_rate_pct"]
    assert candidate["max_drawdown_inr"] < baseline["max_drawdown_inr"]
    assert candidate["net_pnl_inr"] > baseline["net_pnl_inr"]
    assert candidate["net_pnl_inr"] < 0
    assert candidate["profit_factor"] < 1.0


def test_holdout_failed_without_posthoc_side_filter():
    record = STRATEGY_F5_SCORE_GE2_APRIL_RECORDED_FINDINGS_V1
    assert record["validation_gate"]["passed"] is False
    assert record["decision"] == (
        "REJECT_SCORE_GE2_AS_ECONOMICALLY_VALIDATED_F5_ENTRY_FILTER"
    )
    assert record["guardrails"]["no_side_filter_from_april"] is True
    assert record["guardrails"]["no_score_cutoff_search_on_april"] is True
