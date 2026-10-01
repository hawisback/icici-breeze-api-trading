from services.historical.strategy_f2_august_recorded_findings import (
    STRATEGY_F2_AUGUST_RECORDED_FINDINGS_V1,
)


def test_f2_august_record_is_sha_bound():
    record = STRATEGY_F2_AUGUST_RECORDED_FINDINGS_V1
    assert record["source"]["backtest_artifact_sha256"] == (
        "01e2d5d3d4c82414e24dadc6fd39fb45e385cfbcf64bd243c232e936c745cf4e"
    )
    assert record["source"]["market_artifact_sha256"] == (
        "c1ee144f0a4421351f1fd403e89627af1177493470fd8e55a7ef511c7df8662d"
    )
    assert record["source"]["complete_sessions"] == 21


def test_both_f2_august_variants_are_positive_after_costs():
    record = STRATEGY_F2_AUGUST_RECORDED_FINDINGS_V1
    assert record["F2_1BAR"]["total_net_pnl_inr"] > 0
    assert record["F2_1BAR"]["profit_factor"] > 1.0
    assert record["F2_2BAR"]["total_net_pnl_inr"] > 0
    assert record["F2_2BAR"]["profit_factor"] > 1.0


def test_f2_2bar_is_more_slippage_resilient_but_not_selected_yet():
    record = STRATEGY_F2_AUGUST_RECORDED_FINDINGS_V1
    assert record["F2_1BAR"]["slippage_net_pnl_inr"]["1.0"] < 0
    assert record["F2_2BAR"]["slippage_net_pnl_inr"]["1.0"] > 0
    assert record["guardrails"][
        "do_not_select_confirmation_depth_from_august_alone"
    ] is True


def test_f2_august_direction_asymmetry_not_promoted():
    record = STRATEGY_F2_AUGUST_RECORDED_FINDINGS_V1
    assert record["F2_2BAR"]["call_leg"]["net_pnl_inr"] > 0
    assert record["F2_2BAR"]["put_leg"]["net_pnl_inr"] < 0
    assert record["guardrails"]["do_not_select_option_side_from_august_alone"] is True
