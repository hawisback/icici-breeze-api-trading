from services.historical.independent_historical_backfill_provenance_recorded import (
    HISTORICAL_BACKFILL_PROVENANCE_RECONCILIATION_V1,
)


def test_provenance_reconciliation_pins_old_report_and_expiry_fix():
    record = HISTORICAL_BACKFILL_PROVENANCE_RECONCILIATION_V1

    assert record["old_backfill_report"]["commit"] == (
        "d8f167f2831ffb13389be715d1a2df56fb57d82b"
    )
    assert record["old_backfill_report"]["reported_futures_sessions"] == 402
    assert record["old_backfill_report"]["reported_march_2026_expiry"] == (
        "2026-03-31"
    )
    assert record["expiry_fix"]["commit"] == (
        "96e6dbf66a0566512d7e9e0a2167201714f90e7f"
    )
    assert record["expiry_fix"]["scheduled_expiry"] == "2026-03-31"
    assert record["expiry_fix"]["exchange_valid_expiry"] == "2026-03-30"


def test_provenance_reconciliation_explains_exact_19_session_difference():
    current = HISTORICAL_BACKFILL_PROVENANCE_RECONCILIATION_V1[
        "current_database_inventory"
    ]

    assert current["source_database_sha256"] == (
        "3e75de732077aa056c4027ed20f789059f11787fc7102c2de9a6a6ce04cbeee6"
    )
    assert current["database_sessions"] == 421
    assert current["reconciled_session_difference"] == 19
    assert len(current["old_report_missing_dates_now_present"]) == 19
    assert current["remaining_old_report_missing_dates"] == 27
    assert current["old_report_missing_dates_now_present"][0] == "2026-03-02"
    assert current["old_report_missing_dates_now_present"][-1] == "2026-03-30"


def test_provenance_reconciliation_is_non_research_and_non_trading():
    record = HISTORICAL_BACKFILL_PROVENANCE_RECONCILIATION_V1
    guardrails = record["guardrails"]

    assert record["research_only"] is True
    assert record["provenance_only"] is True
    assert guardrails["do_not_rewrite_historical_report"] is True
    assert guardrails["no_research_sample_change"] is True
    assert guardrails["no_outcome_change"] is True
    assert guardrails["no_predictor_or_target_computation"] is True
    assert guardrails["no_strategy_retest"] is True
    assert guardrails["prospective_replication_unchanged"] is True
    assert guardrails["strategy_d_remains_paused"] is True
