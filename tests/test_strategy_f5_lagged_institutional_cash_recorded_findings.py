from services.historical.strategy_f5_lagged_institutional_cash_recorded_findings import (
    STRATEGY_F5_LAGGED_INSTITUTIONAL_CASH_RECORDED_FINDINGS_V1,
)


def test_cash_findings_are_sha_bound_and_complete():
    record = STRATEGY_F5_LAGGED_INSTITUTIONAL_CASH_RECORDED_FINDINGS_V1
    assert record["source"]["artifact_sha256"] == (
        "02b9862f59ac79d633a658080e222f75d305f31607e22d87cc9be898f924810e"
    )
    assert record["source"]["sessions_with_lagged_context"] == 65
    assert record["source"]["matched_trades"] == 443


def test_fii_cash_selling_is_not_promoted_as_bearish_pe_confirmation():
    record = STRATEGY_F5_LAGGED_INSTITUTIONAL_CASH_RECORDED_FINDINGS_V1
    selling = record["bearish_regime"]["PE_prior_FII_selling"]
    not_selling = record["bearish_regime"]["PE_prior_FII_not_selling"]
    assert selling["average_net_pnl_inr"] < not_selling["average_net_pnl_inr"]
    assert record["guardrails"]["no_institutional_cash_filter_promoted"] is True


def test_bearish_pe_cash_relationship_reverses_in_september():
    record = STRATEGY_F5_LAGGED_INSTITUTIONAL_CASH_RECORDED_FINDINGS_V1
    months = record["bearish_pe_by_month"]
    assert months["2026-07"]["FII_selling"]["net_pnl_inr"] < 0
    assert months["2026-08"]["FII_selling"]["net_pnl_inr"] < 0
    assert (
        months["2026-09"]["FII_selling"]["net_pnl_inr"]
        > months["2026-09"]["FII_not_selling"]["net_pnl_inr"]
    )


def test_bullish_fii_buying_does_not_rescue_ce():
    record = STRATEGY_F5_LAGGED_INSTITUTIONAL_CASH_RECORDED_FINDINGS_V1
    buying = record["bullish_regime"]["CE_prior_FII_buying"]
    assert buying["net_pnl_inr"] < 0
    assert record["decision"] == (
        "NO_INSTITUTIONAL_CASH_RULE_PROMOTED_FREEZE_CONTEXT_DISCOVERY"
    )


def test_next_step_freezes_in_sample_feature_search():
    record = STRATEGY_F5_LAGGED_INSTITUTIONAL_CASH_RECORDED_FINDINGS_V1
    assert record["guardrails"]["no_new_in_sample_composite_search"] is True
    assert "out-of-sample holdout" in record["next_step"]
