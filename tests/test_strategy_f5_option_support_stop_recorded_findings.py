from services.historical.strategy_f5_option_support_stop_recorded_findings import (
    STRATEGY_F5_OPTION_SUPPORT_STOP_RECORDED_FINDINGS_V1,
)


def test_record_is_bound_to_uploaded_option_support_artifact():
    record = STRATEGY_F5_OPTION_SUPPORT_STOP_RECORDED_FINDINGS_V1
    assert record["source"]["artifact_sha256"] == (
        "ca4c9b5f4c3c1474662b718ef0bc923fdef0c37413f9be83983fe4c0dd18db24"
    )
    assert record["source"]["target_bearish_PE_trades"] == 84


def test_all_option_support_candidates_failed():
    record = STRATEGY_F5_OPTION_SUPPORT_STOP_RECORDED_FINDINGS_V1
    assert all(not block["passed"] for block in record["candidates"].values())
    assert record["guardrails"]["no_option_support_stop_promoted"] is True


def test_signal_bar_support_saves_losers_but_destroys_more_winner_value():
    record = STRATEGY_F5_OPTION_SUPPORT_STOP_RECORDED_FINDINGS_V1
    block = record["decomposition"]["SIGNAL_BAR_LOW"]
    assert block["pnl_delta_from_triggered_losers_inr"] > 0.0
    assert block["pnl_delta_from_triggered_winners_inr"] < 0.0
    assert abs(block["pnl_delta_from_triggered_winners_inr"]) > (
        block["pnl_delta_from_triggered_losers_inr"]
    )


def test_next_stop_is_preservation_derived_not_pnl_optimized():
    record = STRATEGY_F5_OPTION_SUPPORT_STOP_RECORDED_FINDINGS_V1
    assert record["guardrails"]["no_support_resistance_retuning"] is True
    assert (
        record["guardrails"][
            "next_stop_candidate_must_be_preservation_derived_not_pnl_optimized"
        ]
        is True
    )
