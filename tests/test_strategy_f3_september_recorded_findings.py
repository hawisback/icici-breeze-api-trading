from services.historical.strategy_f3_september_recorded_findings import (
    STRATEGY_F3_SEPTEMBER_RECORDED_FINDINGS_V1,
)


def test_f3_september_record_is_sha_bound():
    record = STRATEGY_F3_SEPTEMBER_RECORDED_FINDINGS_V1
    assert record["source"]["backtest_artifact_sha256"] == (
        "ac1d6103670fa869d665b124a49c9eb2900e90cada6bff405096afae7a2406f9"
    )
    assert record["source"]["market_artifact_sha256"] == (
        "f44907ef39b070514fe175f8f7aecac84049799b6b90e7f99be8a6a9632e0921"
    )
    assert record["source"]["scorable_trades"] == 71
    assert record["source"]["trade_price_coverage_pct"] == 100.0


def test_f3_symmetric_strategy_is_only_marginal_at_zero_slippage():
    record = STRATEGY_F3_SEPTEMBER_RECORDED_FINDINGS_V1
    assert record["overall"]["total_gross_pnl_inr"] > 0
    assert record["overall"]["total_net_pnl_inr"] > 0
    assert record["overall"]["profit_factor"] > 1.0
    assert record["slippage_sensitivity"]["overall"]["0.5"] < 0
    assert record["slippage_sensitivity"]["overall"]["1.0"] < 0


def test_f3_pe_asymmetry_is_strong_but_posthoc():
    record = STRATEGY_F3_SEPTEMBER_RECORDED_FINDINGS_V1
    assert record["ce_leg"]["total_net_pnl_inr"] < 0
    assert record["pe_leg"]["total_net_pnl_inr"] > 0
    assert record["pe_leg"]["profit_factor"] > 1.0
    assert record["slippage_sensitivity"]["pe_leg_posthoc"]["0.5"] > 0
    assert record["slippage_sensitivity"]["pe_leg_posthoc"]["1.0"] > 0
    assert record["pe_leg"]["net_after_removing_largest_winner_inr"] > 0
    assert record["pe_leg"]["net_after_removing_two_largest_winners_inr"] < 0
    assert record["guardrails"]["pe_only_is_posthoc"] is True


def test_f3_september_record_remains_nonexecution():
    guardrails = STRATEGY_F3_SEPTEMBER_RECORDED_FINDINGS_V1["guardrails"]
    assert guardrails["live_execution"] is False
    assert guardrails["paper_execution"] is False
    assert guardrails["broker_orders"] is False
    assert guardrails["no_pe_only_promotion_from_september"] is True
    assert guardrails["no_macd_parameter_tuning_on_september"] is True
