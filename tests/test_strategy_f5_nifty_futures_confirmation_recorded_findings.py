from services.historical.strategy_f5_nifty_futures_confirmation_recorded_findings import (
    STRATEGY_F5_NIFTY_FUTURES_CONFIRMATION_RECORDED_FINDINGS_V1,
)


def test_futures_findings_are_sha_bound():
    record = STRATEGY_F5_NIFTY_FUTURES_CONFIRMATION_RECORDED_FINDINGS_V1
    assert record["source"]["artifact_sha256"] == (
        "302bd4e05107e392bfb8d143d8ac945d87e2f1f08be526cd6aa6d51730865387"
    )
    assert record["source"]["sessions_with_oi"] == 65
    assert record["source"]["matched_trades"] == 443


def test_short_buildup_not_promoted_as_incremental_bearish_pe_edge():
    record = STRATEGY_F5_NIFTY_FUTURES_CONFIRMATION_RECORDED_FINDINGS_V1
    all_pe = record["bearish_regime"]["PE_all"]
    sb = record["bearish_regime"]["PE_short_buildup"]
    non = record["bearish_regime"]["PE_non_short_buildup"]
    assert sb["net_pnl_inr"] > 0
    assert non["net_pnl_inr"] > 0
    assert sb["trail_activation_rate_pct"] < non["trail_activation_rate_pct"]
    assert sb["average_net_pnl_inr"] < non["average_net_pnl_inr"]


def test_next_step_advances_to_breadth_without_futures_rule():
    record = STRATEGY_F5_NIFTY_FUTURES_CONFIRMATION_RECORDED_FINDINGS_V1
    assert record["decision"] == (
        "NO_FUTURES_STATE_RULE_PROMOTED_ADVANCE_TO_BREADTH"
    )
    assert record["guardrails"]["no_futures_state_filter_promoted"] is True
