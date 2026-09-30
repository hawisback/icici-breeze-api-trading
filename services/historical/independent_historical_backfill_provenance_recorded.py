"""Recorded provenance reconciliation for the extended Breeze backfill report.

The committed extended_backfill_report.json predates the March-2026 NIFTY
monthly-expiry override added to breeze_backfill.py. The old report therefore
listed March sessions as missing while querying the scheduled 2026-03-31
contract. The current historical database contains 19 of those dates under the
exchange-valid 2026-03-30 contract after the later correction/repair work.

This reconciliation is provenance-only. It changes no research outcome,
predictor, target, sample, or strategy decision.
"""

HISTORICAL_BACKFILL_PROVENANCE_RECONCILIATION_V1 = {
    "research_type": "NIFTY_HISTORICAL_BACKFILL_PROVENANCE_RECONCILIATION_V1",
    "research_only": True,
    "provenance_only": True,
    "old_backfill_report": {
        "path": "data/extended_breeze_backfill_report.json",
        "commit": "d8f167f2831ffb13389be715d1a2df56fb57d82b",
        "commit_date_utc": "2026-09-19T14:02:32Z",
        "reported_futures_sessions": 402,
        "reported_march_2026_expiry": "2026-03-31",
        "reported_march_2026_contract_rows": 0,
    },
    "expiry_fix": {
        "commit": "96e6dbf66a0566512d7e9e0a2167201714f90e7f",
        "commit_date_utc": "2026-09-22T09:28:43Z",
        "message": "fix(historical): honor revised March 2026 NIFTY expiry",
        "scheduled_expiry": "2026-03-31",
        "exchange_valid_expiry": "2026-03-30",
        "reason": "Mahavir Jayanti exchange holiday override",
    },
    "current_database_inventory": {
        "source_database_sha256": (
            "3e75de732077aa056c4027ed20f789059f11787fc7102c2de9a6a6ce04cbeee6"
        ),
        "database_sessions": 421,
        "old_report_missing_dates_now_present": [
            "2026-03-02",
            "2026-03-04",
            "2026-03-05",
            "2026-03-06",
            "2026-03-09",
            "2026-03-10",
            "2026-03-11",
            "2026-03-12",
            "2026-03-13",
            "2026-03-16",
            "2026-03-17",
            "2026-03-18",
            "2026-03-19",
            "2026-03-20",
            "2026-03-23",
            "2026-03-24",
            "2026-03-25",
            "2026-03-27",
            "2026-03-30",
        ],
        "reconciled_session_difference": 19,
        "remaining_old_report_missing_dates": 27,
    },
    "interpretation": (
        "The 402-session count in the committed September-19 extended backfill "
        "report is stale relative to the current database. The 19-session "
        "difference is explained by March-2026 data becoming available after "
        "the expiry-date correction to the exchange-valid 2026-03-30 contract. "
        "The direct current-database inventory count of 421 sessions is the "
        "appropriate provenance count for the SHA-bound database."
    ),
    "guardrails": {
        "do_not_rewrite_historical_report": True,
        "no_research_sample_change": True,
        "no_outcome_change": True,
        "no_predictor_or_target_computation": True,
        "no_strategy_retest": True,
        "prospective_replication_unchanged": True,
        "strategy_d_remains_paused": True,
    },
}
