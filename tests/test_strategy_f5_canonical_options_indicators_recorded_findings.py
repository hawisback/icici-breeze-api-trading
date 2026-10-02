from services.historical.strategy_f5_canonical_options_indicators_recorded_findings import (
    STRATEGY_F5_CANONICAL_OPTIONS_INDICATORS_RECORDED_FINDINGS_V1,
)


def test_canonical_options_findings_are_sha_bound():
    record = STRATEGY_F5_CANONICAL_OPTIONS_INDICATORS_RECORDED_FINDINGS_V1
    assert record["source"]["diagnostic_artifact_sha256"] == (
        "08251543629a572b5681eab8bb20058d27ecf64a743474853109a962e49dddc3"
    )
    assert record["source"]["trades"] == 443
    assert record["source"]["oi_coverage_pct"] == 100.0


def test_atr_bb_macd_beat_short_horizon_oi_change():
    record = STRATEGY_F5_CANONICAL_OPTIONS_INDICATORS_RECORDED_FINDINGS_V1
    strong = record["strongest_canonical_indicators"]
    weak = record["weak_oi_change_metrics"]
    assert strong["atr14_pct"]["activation_auc"] > 0.60
    assert strong["bb_bandwidth20_pct"]["activation_auc"] > 0.60
    assert strong["macd_hist_pct"]["activation_auc"] > 0.60
    assert abs(weak["oi_change1_pct"]["activation_auc"] - 0.5) < 0.02
    assert abs(weak["oi_change3_pct"]["activation_auc"] - 0.5) < 0.02


def test_no_oi_state_filter_is_promoted():
    record = STRATEGY_F5_CANONICAL_OPTIONS_INDICATORS_RECORDED_FINDINGS_V1
    assert record["decision"] == (
        "PRIORITIZE_ATR_BOLLINGER_MACD_STOCHASTIC_WITH_OI_AS_SECONDARY_CONFIRMATION"
    )
    assert record["guardrails"]["do_not_use_short_horizon_oi_change_as_filter"]
    assert record["guardrails"]["do_not_promote_price_oi_state_filter"]
