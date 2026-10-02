from services.historical.strategy_f5_four_factor_quality_score_recorded_findings import (
    STRATEGY_F5_FOUR_FACTOR_QUALITY_SCORE_RECORDED_FINDINGS_V1,
)


def test_quality_score_findings_are_sha_bound():
    record = STRATEGY_F5_FOUR_FACTOR_QUALITY_SCORE_RECORDED_FINDINGS_V1
    assert record["source"]["artifact_sha256"] == (
        "fea4565b13321d720080f5036e9d0ec6c65b65e5ad58781013a26001a94a0abf"
    )
    assert record["source"]["matched_trades"] == 443


def test_quality_score_orders_activation_and_bad_trade_rate_overall():
    record = STRATEGY_F5_FOUR_FACTOR_QUALITY_SCORE_RECORDED_FINDINGS_V1
    buckets = record["overall_score_buckets"]
    scores = ["0", "1", "2", "3", "4"]
    bad = [buckets[s]["bad_trade_rate_pct"] for s in scores]
    activation = [buckets[s]["trail_activation_rate_pct"] for s in scores]
    assert bad == sorted(bad, reverse=True)
    assert activation == sorted(activation)


def test_score_ge_2_selected_by_existing_preservation_rule_not_max_pnl():
    record = STRATEGY_F5_FOUR_FACTOR_QUALITY_SCORE_RECORDED_FINDINGS_V1
    selected = record["selection_rule_for_fresh_holdout"]
    ge2 = record["cumulative_cutoffs_descriptive_only"]["score_ge_2"]
    ge3 = record["cumulative_cutoffs_descriptive_only"]["score_ge_3"]
    assert selected["rule"] == "QUALITY_SCORE_GE_2"
    assert ge2["activated_signal_capture_pct"] >= 65.0
    assert ge2["winner_signal_capture_pct"] >= 65.0
    assert ge3["activated_signal_capture_pct"] < 65.0
    assert ge3["winner_signal_capture_pct"] < 65.0
    assert selected["not_selected_by_maximum_pnl"] is True
