from services.historical.strategy_f3_august_replication_recorded_findings import (
    STRATEGY_F3_AUGUST_REPLICATION_RECORDED_FINDINGS_V1,
)


def test_f3_august_record_is_sha_bound():
    record = STRATEGY_F3_AUGUST_REPLICATION_RECORDED_FINDINGS_V1
    assert record["source"]["backtest_artifact_sha256"] == (
        "e6d25f2cdd895e5d540b85a38d88ef4957c9255e8f6564864c63add264699b5a"
    )
    assert record["source"]["market_artifact_sha256"] == (
        "97cbc53c85d1e6c6da905f896e988f5bcbbe1816eaed0aa4e8b4a5a1ce97f271"
    )
    assert record["source"]["scorable_trades"] == 60
    assert record["source"]["trade_price_coverage_pct"] == 100.0


def test_f3_august_full_strategy_is_positive_at_zero_and_half_point():
    record = STRATEGY_F3_AUGUST_RECORDED_FINDINGS_V1
    assert record["overall"]["total_net_pnl_inr"] > 0
    assert record["overall"]["profit_factor"] > 1.0
    assert record["full_strategy_slippage_net_pnl_inr"]["0.5"] > 0
    assert record["full_strategy_slippage_net_pnl_inr"]["1.0"] < 0


def test_f3_pe_asymmetry_replicates_and_survives_one_point_slippage():
    record = STRATEGY_F3_AUGUST_RECORDED_FINDINGS_V1
    assert record["pe_leg"]["total_net_pnl_inr"] > 0
    assert record["pe_leg"]["profit_factor"] > 1.0
    assert record["pe_leg"]["slippage_net_pnl_inr"]["1.0"] > 0
    assert record["ce_leg"]["total_net_pnl_inr"] < 0
    assert record["ce_leg"]["slippage_net_pnl_inr"]["1.0"] < 0


def test_cross_month_pe_candidate_is_positive_in_both_months():
    record = STRATEGY_F3_AUGUST_RECORDED_FINDINGS_V1
    combined = record["cross_month_august_september"]["pe_leg"]
    assert combined["trades"] == 66
    assert combined["net_pnl_inr"]["0.0"] > 0
    assert combined["net_pnl_inr"]["0.5"] > 0
    assert combined["net_pnl_inr"]["1.0"] > 0
    assert combined["profit_factor"]["1.0"] > 1.0
    assert len(combined["months_positive_after_costs"]) == 2
    assert record["status"] == "PE_ASYMMETRY_REPLICATED_ADVANCE_PE_ONLY_CANDIDATE"


def test_f3_pe_candidate_remains_research_only():
    guardrails = STRATEGY_F3_AUGUST_RECORDED_FINDINGS_V1["guardrails"]
    assert guardrails["live_execution"] is False
    assert guardrails["paper_execution"] is False
    assert guardrails["broker_orders"] is False
    assert guardrails["pe_only_candidate_requires_new_month"] is True
