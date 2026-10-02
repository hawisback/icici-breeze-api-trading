from services.historical.strategy_f5_lagged_institutional_oi_recorded_findings import (
    STRATEGY_F5_LAGGED_INSTITUTIONAL_OI_RECORDED_FINDINGS_V1,
)


def test_lagged_institutional_findings_are_sha_bound():
    record = STRATEGY_F5_LAGGED_INSTITUTIONAL_OI_RECORDED_FINDINGS_V1
    assert record["source"]["artifact_sha256"] == (
        "7449ffd06ad28f41b919f8411c000dce868a40df8bd2b94e67ecf6db5321a53e"
    )
    assert record["source"]["sessions_with_lagged_context"] == 65
    assert record["source"]["matched_trades"] == 443


def test_fii_position_level_has_no_discriminating_power_in_window():
    record = STRATEGY_F5_LAGGED_INSTITUTIONAL_OI_RECORDED_FINDINGS_V1
    assert record["position_state_coverage"]["FII"]["all_sessions_state"] == (
        "NET_SHORT"
    )
    assert record["position_state_coverage"]["DII"]["all_sessions_state"] == (
        "NET_LONG"
    )


def test_fii_more_short_is_not_promoted_as_bearish_pe_confirmation():
    record = STRATEGY_F5_LAGGED_INSTITUTIONAL_OI_RECORDED_FINDINGS_V1
    more_short = record["bearish_regime"]["PE_prior_FII_more_short"]
    not_more_short = record["bearish_regime"]["PE_prior_FII_not_more_short"]
    assert more_short["average_net_pnl_inr"] < not_more_short["average_net_pnl_inr"]
    assert more_short["trail_activation_rate_pct"] < (
        not_more_short["trail_activation_rate_pct"]
    )
    assert record["decision"] == (
        "NO_INSTITUTIONAL_OI_RULE_PROMOTED_CONTINUE_TO_LAGGED_CASH_FLOW"
    )


def test_dii_change_clue_is_retained_only_as_discovery():
    record = STRATEGY_F5_LAGGED_INSTITUTIONAL_OI_RECORDED_FINDINGS_V1
    clue = record["dii_change_discovery"]
    assert clue["bearish_PE_prior_DII_more_long"]["net_pnl_inr"] > 0
    assert all(
        value > 0
        for value in clue["more_long_monthly_net_pnl_inr"].values()
    )
    assert record["guardrails"]["no_institutional_oi_filter_promoted"] is True
