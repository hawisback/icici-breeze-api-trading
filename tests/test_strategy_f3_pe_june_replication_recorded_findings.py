from services.historical.strategy_f3_pe_june_replication_recorded_findings import (
    STRATEGY_F3_PE_JUNE_REPLICATION_RECORDED_FINDINGS_V1,
)


def test_june_pe_record_is_sha_bound():
    record = STRATEGY_F3_PE_JUNE_REPLICATION_RECORDED_FINDINGS_V1
    assert record["source"]["backtest_artifact_sha256"] == (
        "7f71026952119473f8473ff8f4935bf40200382fa7c01388ea88fb1049b044fc"
    )
    assert record["source"]["market_artifact_sha256"] == (
        "0f746a3c41a46d7d3e4a5490963944c5e0540604fa18800ffd326b728f973ecf"
    )
    assert record["source"]["trade_price_coverage_pct"] == 100.0


def test_june_pe_replication_failed_decisively():
    record = STRATEGY_F3_PE_JUNE_REPLICATION_RECORDED_FINDINGS_V1
    assert record["summary"]["total_gross_pnl_inr"] < 0
    assert record["summary"]["total_net_pnl_inr"] < 0
    assert record["summary"]["profit_factor"] < 1.0
    assert record["slippage_net_pnl_inr"]["0.5"] < 0
    assert record["slippage_net_pnl_inr"]["1.0"] < 0
    assert record["replication_gate"]["passed"] is False


def test_pe_only_branch_is_closed_without_same_sample_rescue():
    record = STRATEGY_F3_PE_JUNE_REPLICATION_RECORDED_FINDINGS_V1
    assert record["status"] == (
        "PE_ONLY_REPLICATION_FAILED_CLOSE_SIDE_SELECTION_BRANCH"
    )
    assert record["guardrails"]["no_pe_only_rescue_on_june"] is True
    assert record["guardrails"]["no_time_of_day_filter_from_june"] is True
    assert record["guardrails"][
        "no_macd_parameter_tuning_on_inspected_months"
    ] is True
