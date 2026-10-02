from services.historical.strategy_f_macd_options_recorded_findings import (
    STRATEGY_F_MACD_OPTIONS_RECORDED_FINDINGS_V1,
)


def test_strategy_f_record_is_sha_bound():
    record = STRATEGY_F_MACD_OPTIONS_RECORDED_FINDINGS_V1
    assert record["source"]["backtest_artifact_sha256"] == (
        "5392929a14f56a656708b90eb9ad1d4c8cca3da7d2982d66fedc7ae7bca90f91"
    )
    assert record["source"]["market_artifact_sha256"] == (
        "ac22701ea605dbedd023049f4355479cf1c5cc9a60494ab56b9e1f70204caae4"
    )
    assert record["source"]["complete_sessions"] == 21
    assert record["source"]["scorable_trades"] == 109
    assert record["source"]["skipped_trades"] == 0
    assert record["source"]["trade_price_coverage_pct"] == 100.0


def test_strategy_f_v1_is_rejected_overall():
    record = STRATEGY_F_MACD_OPTIONS_RECORDED_FINDINGS_V1
    assert record["overall"]["total_gross_pnl_inr"] < 0
    assert record["overall"]["total_net_pnl_inr"] < 0
    assert record["overall"]["profit_factor"] < 1.0
    assert record["decision"] == (
        "STRATEGY_F_V1_SYMMETRIC_MACD_CROSSOVER_REJECTED_FOR_SEPTEMBER_2026"
    )


def test_pe_asymmetry_remains_posthoc_only():
    record = STRATEGY_F_MACD_OPTIONS_RECORDED_FINDINGS_V1
    assert record["call_leg"]["total_net_pnl_inr"] < 0
    assert record["put_leg"]["total_net_pnl_inr"] > 0
    assert record["put_leg"]["profit_factor"] > 1.0
    assert record["put_leg"]["posthoc_only"] is True
    assert record["guardrails"]["pe_only_is_posthoc_diagnostic"] is True
    assert record["guardrails"]["no_same_month_parameter_optimization"] is True
    assert record["guardrails"]["no_same_month_filter_search"] is True


def test_whipsaw_diagnostic_is_not_promoted_to_entry_rule():
    diag = STRATEGY_F_MACD_OPTIONS_RECORDED_FINDINGS_V1[
        "posthoc_diagnostics"
    ]
    assert diag["trades_holding_15_minutes_or_less"] == 20
    assert diag["wins_holding_15_minutes_or_less"] == 0
    assert diag["net_pnl_holding_15_minutes_or_less_inr"] < 0
    assert diag["trades_holding_more_than_60_minutes"] == 49
    assert diag["net_pnl_holding_more_than_60_minutes_inr"] > 0


def test_strategy_f_record_remains_nonexecution():
    guardrails = STRATEGY_F_MACD_OPTIONS_RECORDED_FINDINGS_V1["guardrails"]
    assert guardrails["backtest_only"] is True
    assert guardrails["live_execution"] is False
    assert guardrails["paper_execution"] is False
    assert guardrails["broker_orders"] is False
    assert guardrails["strategy_d_remains_paused"] is True
