from services.historical.strategy_f5_nifty_structure_stop_recorded_findings import (
    STRATEGY_F5_NIFTY_STRUCTURE_STOP_RECORDED_FINDINGS_V1,
)


def test_record_is_bound_to_uploaded_structure_stop_artifact():
    record = STRATEGY_F5_NIFTY_STRUCTURE_STOP_RECORDED_FINDINGS_V1
    assert record["source"]["artifact_sha256"] == (
        "453b241d47952e646f1b8aea9c55e7599e470ab53bb02fb072cb8203c06ec96a"
    )
    assert record["source"]["target_bearish_PE_trades"] == 84


def test_no_nifty_resistance_candidate_passed():
    record = STRATEGY_F5_NIFTY_STRUCTURE_STOP_RECORDED_FINDINGS_V1
    assert all(not block["passed"] for block in record["candidates"].values())
    assert record["guardrails"]["no_nifty_resistance_stop_promoted"] is True


def test_next_step_moves_to_option_support_without_retuning_nifty():
    record = STRATEGY_F5_NIFTY_STRUCTURE_STOP_RECORDED_FINDINGS_V1
    assert record["guardrails"]["no_nifty_resistance_retuning"] is True
    assert record["guardrails"]["move_to_option_support_structure"] is True
