from services.historical.strategy_f3_july_replication_recorded_findings import (
    STRATEGY_F3_JULY_REPLICATION_RECORDED_FINDINGS_V1,
)


def test_f3_july_record_is_sha_bound():
    record = STRATEGY_F3_JULY_REPLICATION_RECORDED_FINDINGS_V1
    assert record["source"]["backtest_artifact_sha256"] == (
        "ddaf215936afc43247d7d62ffde9f44f932cf9875603afffbadabaaaa5b571ec"
    )
    assert record["source"]["market_artifact_sha256"] == (
        "1ab7ac11f01be97336f67cc114c9aecaef01e6e955eb00a7b8d05f14b2a8abe7"
    )
    assert record["source"]["trade_price_coverage_pct"] == 100.0


def test_full_f3_july_failed_before_and_after_costs():
    record = STRATEGY_F3_JULY_REPLICATION_RECORDED_FINDINGS_V1
    assert record["overall"]["total_gross_pnl_inr"] < 0
    assert record["overall"]["total_net_pnl_inr"] < 0
    assert record["overall"]["profit_factor"] < 1.0
    assert record["slippage_net_pnl_inr"]["0.5"] < 0
    assert record["slippage_net_pnl_inr"]["1.0"] < 0


def test_both_option_sides_failed_in_july():
    record = STRATEGY_F3_JULY_REPLICATION_RECORDED_FINDINGS_V1
    assert record["ce_leg"]["total_net_pnl_inr"] < 0
    assert record["ce_leg"]["profit_factor"] < 1.0
    assert record["pe_leg"]["total_net_pnl_inr"] < 0
    assert record["pe_leg"]["profit_factor"] < 1.0


def test_raw_crossover_branch_is_closed_without_posthoc_rescue():
    record = STRATEGY_F3_JULY_REPLICATION_RECORDED_FINDINGS_V1
    assert record["decision"] == "FULL_OPTION_NATIVE_MACD_RAW_CROSSOVER_NOT_ROBUST"
    assert record["status"] == (
        "CLOSE_RAW_CROSSOVER_BRANCH_NO_SAME_SAMPLE_FILTER_RESCUE"
    )
    assert record["posthoc_diagnostics"]["late_session_1330_1515"]["posthoc_only"] is True
    assert record["guardrails"]["no_time_of_day_filter_from_july"] is True
    assert record["guardrails"]["no_side_filter_from_inspected_months"] is True
    assert record["guardrails"][
        "no_macd_parameter_tuning_on_inspected_months"
    ] is True
