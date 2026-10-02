from services.historical.strategy_f5_bearish_pe_late_entry_june_recorded_findings import (
    STRATEGY_F5_BEARISH_PE_LATE_ENTRY_JUNE_RECORDED_FINDINGS_V1,
)


def test_june_record_is_bound_to_uploaded_artifact():
    record = STRATEGY_F5_BEARISH_PE_LATE_ENTRY_JUNE_RECORDED_FINDINGS_V1
    assert record["source"]["artifact_sha256"] == (
        "08f764ae82c289fc344045d01b426870008f3d95b7797e6c668a7f3811a8e93b"
    )
    assert record["source"]["selected_sessions"] == 21


def test_hard_1430_block_failed_preservation_gate():
    record = STRATEGY_F5_BEARISH_PE_LATE_ENTRY_JUNE_RECORDED_FINDINGS_V1
    gate = record["validation_gate"]
    assert gate["passed"] is False
    assert gate["winner_capture_pct"] < gate["required_capture_pct"]
    assert gate["activation_capture_pct"] < gate["required_capture_pct"]


def test_late_subset_was_negative_but_not_whole_explanation():
    record = STRATEGY_F5_BEARISH_PE_LATE_ENTRY_JUNE_RECORDED_FINDINGS_V1
    target = record["matched_entry_time_bearish_PE"]
    assert target["late_ge_1430"]["net_pnl_inr"] < 0.0
    assert target["before_1430"]["net_pnl_inr"] < 0.0
    assert record["guardrails"]["no_1430_hard_block_promoted"] is True


def test_forward_use_is_descriptive_only():
    record = STRATEGY_F5_BEARISH_PE_LATE_ENTRY_JUNE_RECORDED_FINDINGS_V1
    assert record["guardrails"]["forward_late_entry_reporting_descriptive_only"] is True
    assert record["guardrails"]["no_june_retuning"] is True
