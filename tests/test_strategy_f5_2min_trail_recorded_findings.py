from services.historical.strategy_f5_2min_trail_recorded_findings import (
    STRATEGY_F5_2MIN_TRAIL_RECORDED_FINDINGS_V1,
)


def test_f5_record_is_sha_bound():
    record = STRATEGY_F5_2MIN_TRAIL_RECORDED_FINDINGS_V1
    assert record["source"]["backtest_artifact_sha256"] == (
        "0717c493aff5460d625b588771caa4acb6cc152a1ffc97c0da50cd7d6bf9d0e0"
    )
    assert record["source"]["market_artifact_sha256"] == (
        "0ed1b1499505e53c310deddbb2e9a6b78bba48c8c23fab94639dc2fa6765e2a9"
    )


def test_f5_trail_did_not_improve_total_pnl():
    record = STRATEGY_F5_2MIN_TRAIL_RECORDED_FINDINGS_V1
    raw = record["raw_macd_exit"]
    trail = record["trail10_close_confirmed"]
    assert raw["net_pnl_inr"] < 0
    assert trail["net_pnl_inr"] < raw["net_pnl_inr"]
    assert trail["net_delta_vs_raw_macd_exit_inr"] < 0


def test_f5_only_minority_of_entries_activate_trail():
    record = STRATEGY_F5_2MIN_TRAIL_RECORDED_FINDINGS_V1
    trail = record["trail10_close_confirmed"]
    assert trail["trail_activation_rate_pct"] < 30.0
    assert trail["exit_reason_counts"]["PRE_TRAIL_BEARISH_MACD_CROSS"] > (
        trail["exit_reason_counts"]["CLOSE_CONFIRMED_TRAIL10"]
    )


def test_f5_entry_selectivity_is_next_question_not_trail_tuning():
    record = STRATEGY_F5_2MIN_TRAIL_RECORDED_FINDINGS_V1
    assert record["decision"] == (
        "ENTRY_SELECTIVITY_IS_PRIMARY_F5_PROBLEM_KEEP_TRAIL_FROZEN"
    )
    assert record["guardrails"]["do_not_tune_trail_width_on_jul_sep"] is True
    assert record["guardrails"]["do_not_use_reaches_10pct_as_entry_filter"] is True
    assert record["guardrails"]["live_execution"] is False
