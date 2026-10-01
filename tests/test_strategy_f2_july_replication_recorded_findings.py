from services.historical.strategy_f2_july_replication_recorded_findings import (
    STRATEGY_F2_JULY_REPLICATION_RECORDED_FINDINGS_V1,
)


def test_f2_july_replication_record_is_sha_bound():
    record = STRATEGY_F2_JULY_REPLICATION_RECORDED_FINDINGS_V1
    assert record["source"]["backtest_artifact_sha256"] == (
        "7db0262543e8f42b1bb2bd284c6a5cca056f0ea0b4113f36414257c209bbfaa4"
    )
    assert record["source"]["market_artifact_sha256"] == (
        "2677e9aca7e7d366f838e64962c9a555b9eff3b37244acfc3f788cc84211bc97"
    )


def test_both_f2_variants_fail_july_before_and_after_costs():
    record = STRATEGY_F2_JULY_REPLICATION_RECORDED_FINDINGS_V1
    for name in ("F2_1BAR", "F2_2BAR"):
        variant = record[name]
        assert variant["total_gross_pnl_inr"] < 0
        assert variant["total_net_pnl_inr"] < 0
        assert variant["profit_factor"] < 1.0


def test_f2_2bar_fails_on_both_option_sides():
    record = STRATEGY_F2_JULY_REPLICATION_RECORDED_FINDINGS_V1["F2_2BAR"]
    assert record["call_leg"]["net_pnl_inr"] < 0
    assert record["call_leg"]["profit_factor"] < 1.0
    assert record["put_leg"]["net_pnl_inr"] < 0
    assert record["put_leg"]["profit_factor"] < 1.0


def test_confirmation_depth_branch_is_closed():
    record = STRATEGY_F2_JULY_REPLICATION_RECORDED_FINDINGS_V1
    assert record["status"] == "REPLICATION_FAILED_CLOSE_CONFIRMATION_DEPTH_BRANCH"
    assert record["guardrails"]["no_more_confirmation_depth_tuning_on_july_august"] is True
    assert record["guardrails"]["no_option_side_selection_from_inspected_months"] is True
    assert record["guardrails"]["no_macd_parameter_tuning_on_inspected_months"] is True
