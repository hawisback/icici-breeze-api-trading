from services.historical.strategy_f4_relative_volatility_index_recorded_findings import (
    STRATEGY_F4_RELATIVE_VOLATILITY_INDEX_RECORDED_FINDINGS_V1,
)


def test_f4_corrected_rvi_record_is_sha_bound():
    record = STRATEGY_F4_RELATIVE_VOLATILITY_INDEX_RECORDED_FINDINGS_V1
    assert record["source"]["backtest_artifact_sha256"] == (
        "a1bdc2df219950a5e6ea2707f4b303a71f492953c81212df7f0e0c0a8641f7cf"
    )
    assert record["source"]["market_artifact_sha256"] == (
        "1fb3e038f09dbb3aa55f2bd196c9b5d7982c56908ca4dc402e19f7227ade2840"
    )


def test_low_rvi_thresholds_do_not_robustify_macd():
    record = STRATEGY_F4_RELATIVE_VOLATILITY_INDEX_RECORDED_FINDINGS_V1
    for threshold in (50, 55, 60):
        assert record["thresholds"][threshold]["net_pnl_inr"] < 0
        assert record["thresholds"][threshold]["profit_factor"] < 1.0


def test_rvi75_is_promising_but_underpowered():
    record = STRATEGY_F4_RELATIVE_VOLATILITY_INDEX_RECORDED_FINDINGS_V1
    rvi75 = record["thresholds"][75]
    assert rvi75["net_pnl_inr"] > 0
    assert rvi75["profit_factor"] > 1.0
    assert rvi75["net_at_1_0_slippage_inr"] > 0
    assert all(
        month["net_pnl_inr"] > 0
        for month in rvi75["monthly"].values()
    )
    assert rvi75["trades"] == 11
    assert rvi75["sample_size_warning"] is True
    assert record["guardrails"]["do_not_promote_rvi75_from_11_trades"] is True


def test_pe_split_is_not_promoted_from_small_sample():
    record = STRATEGY_F4_RELATIVE_VOLATILITY_INDEX_RECORDED_FINDINGS_V1
    rvi75 = record["thresholds"][75]
    assert rvi75["ce_leg"]["net_pnl_inr"] < 0
    assert rvi75["pe_leg"]["net_pnl_inr"] > 0
    assert rvi75["pe_leg"]["trades"] == 6
    assert record["guardrails"]["do_not_promote_pe_only_from_6_trades"] is True
