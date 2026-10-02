from services.historical.strategy_f5_pretrail_loss_control_recorded_findings import (
    STRATEGY_F5_PRETRAIL_LOSS_CONTROL_RECORDED_FINDINGS_V1,
)


def test_f5_fixed_stop_findings_are_sha_bound():
    record = STRATEGY_F5_PRETRAIL_LOSS_CONTROL_RECORDED_FINDINGS_V1
    assert record["source"]["diagnostic_artifact_sha256"] == (
        "bb53ae8493786ec98a73ad438343b3357a1bae1b99bc3cac419a966fcdbb2264"
    )
    assert record["source"]["matched_trades"] == 443


def test_no_fixed_stop_improved_baseline():
    record = STRATEGY_F5_PRETRAIL_LOSS_CONTROL_RECORDED_FINDINGS_V1
    for candidate in record["fixed_stop_candidates"].values():
        assert candidate["net_delta_vs_baseline_inr"] < 0


def test_wide_stop_preserves_winners_but_still_fails_economically():
    record = STRATEGY_F5_PRETRAIL_LOSS_CONTROL_RECORDED_FINDINGS_V1
    stop20 = record["fixed_stop_candidates"]["20"]
    assert stop20["activated_trade_untouched_pct"] >= 95.0
    assert stop20["baseline_winner_untouched_pct"] >= 95.0
    assert stop20["net_delta_vs_baseline_inr"] < 0
    assert stop20["monthly_net_delta_zero_slippage_inr"]["2026-07"] < 0
    assert stop20["monthly_net_delta_zero_slippage_inr"]["2026-09"] < 0


def test_fixed_stop_branch_is_closed_without_more_threshold_search():
    record = STRATEGY_F5_PRETRAIL_LOSS_CONTROL_RECORDED_FINDINGS_V1
    assert record["decision"] == "CLOSE_FIXED_PERCENT_PRETRAIL_STOP_BRANCH"
    assert record["guardrails"][
        "do_not_search_more_fixed_percentage_stops_on_jul_sep"
    ] is True
    assert record["guardrails"]["keep_existing_post_activation_trail_frozen"] is True
