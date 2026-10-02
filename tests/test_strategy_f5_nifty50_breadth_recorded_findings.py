from services.historical.strategy_f5_nifty50_breadth_recorded_findings import (
    STRATEGY_F5_NIFTY50_BREADTH_RECORDED_FINDINGS_V1,
)


def test_corrected_breadth_findings_are_sha_bound():
    record = STRATEGY_F5_NIFTY50_BREADTH_RECORDED_FINDINGS_V1
    assert record["source"]["artifact_sha256"] == (
        "a164d7ec2ce3368dba452fd038d00837c6934b38eb9955b9af7ff840349ff18f"
    )
    assert record["source"]["matched_trades"] == 443
    assert record["source"]["breadth_available_sessions"] == 60


def test_missing_breadth_is_separate_and_month_instability_is_recorded():
    record = STRATEGY_F5_NIFTY50_BREADTH_RECORDED_FINDINGS_V1
    assert record["bearish_regime"]["PE_breadth_unavailable"]["trades"] == 7
    months = record["bearish_pe_breadth_confirmed_by_month"]
    assert months["2026-07"]["net_pnl_inr"] > 0
    assert months["2026-08"]["net_pnl_inr"] < 0
    assert months["2026-09"]["net_pnl_inr"] > 0


def test_next_step_advances_to_lagged_institutional_context():
    record = STRATEGY_F5_NIFTY50_BREADTH_RECORDED_FINDINGS_V1
    assert record["decision"] == (
        "NO_BREADTH_RULE_PROMOTED_ADVANCE_TO_LAGGED_INSTITUTIONAL_CONTEXT"
    )
    assert record["guardrails"]["no_breadth_filter_promoted"] is True
