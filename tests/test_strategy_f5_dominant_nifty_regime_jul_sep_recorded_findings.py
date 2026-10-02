from services.historical.strategy_f5_dominant_nifty_regime_jul_sep_recorded_findings import (
    STRATEGY_F5_DOMINANT_NIFTY_REGIME_JUL_SEP_RECORDED_FINDINGS_V1,
)


def test_recorded_findings_are_sha_bound():
    record = STRATEGY_F5_DOMINANT_NIFTY_REGIME_JUL_SEP_RECORDED_FINDINGS_V1
    assert record["source"]["artifact_sha256"] == (
        "fa01c0b396fd5cf1b9ee93d33b56d3502a564e3ecd6f6f867765dfebeb9c89b2"
    )
    assert record["source"]["sessions"] == 65
    assert record["source"]["trades"] == 443


def test_bearish_pe_advantage_holds_all_three_months():
    record = STRATEGY_F5_DOMINANT_NIFTY_REGIME_JUL_SEP_RECORDED_FINDINGS_V1
    for month, block in record["bearish_regime_by_month"].items():
        assert block["PE_net_pnl_inr"] > 0
        assert block["CE_net_pnl_inr"] < 0


def test_next_step_is_futures_confirmation_without_rule_promotion():
    record = STRATEGY_F5_DOMINANT_NIFTY_REGIME_JUL_SEP_RECORDED_FINDINGS_V1
    assert record["decision"] == "ADVANCE_TO_NIFTY_FUTURES_CONFIRMATION"
    assert record["guardrails"]["no_side_filter_promoted_yet"] is True
