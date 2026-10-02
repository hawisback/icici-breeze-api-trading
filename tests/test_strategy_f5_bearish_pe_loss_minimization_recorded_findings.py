from services.historical.strategy_f5_bearish_pe_loss_minimization_recorded_findings import (
    STRATEGY_F5_BEARISH_PE_LOSS_MINIMIZATION_RECORDED_FINDINGS_V1,
)


def test_record_is_bound_to_uploaded_artifact():
    record = STRATEGY_F5_BEARISH_PE_LOSS_MINIMIZATION_RECORDED_FINDINGS_V1
    assert record["source"]["artifact_sha256"] == (
        "bcd44b7a243baf99d333f4eb77b92081603dd21b3650ce88042a0efb80d97ef6"
    )
    assert record["source"]["target_bearish_PE_trades"] == 84


def test_all_frozen_lack_of_progress_exits_failed():
    record = STRATEGY_F5_BEARISH_PE_LOSS_MINIMIZATION_RECORDED_FINDINGS_V1
    for candidate in record["frozen_lack_of_progress_candidates"].values():
        assert candidate["passed"] is False
        assert candidate["net_delta_vs_baseline_inr"] < 0.0


def test_late_1430_clue_is_explicitly_posthoc():
    record = STRATEGY_F5_BEARISH_PE_LOSS_MINIMIZATION_RECORDED_FINDINGS_V1
    clue = record["exploratory_late_entry_clue"]
    assert clue["status"] == "POSTHOC_DEVELOPMENT_CLUE_NOT_VALIDATION"
    assert clue["cutoff"] == "14:30"
    assert clue["entries_at_or_after_cutoff"]["net_pnl_inr"] < 0.0
    assert (
        clue["entries_before_cutoff"][
            "winner_preservation_if_late_entries_removed_pct"
        ]
        >= 95.0
    )
    assert (
        clue["entries_before_cutoff"][
            "activation_preservation_if_late_entries_removed_pct"
        ]
        >= 95.0
    )


def test_next_step_forbids_more_jul_sep_time_search():
    record = STRATEGY_F5_BEARISH_PE_LOSS_MINIMIZATION_RECORDED_FINDINGS_V1
    assert record["guardrails"]["no_more_time_cutoff_search_on_jul_sep"] is True
    assert record["guardrails"]["june_holdout_must_not_retune_cutoff"] is True
