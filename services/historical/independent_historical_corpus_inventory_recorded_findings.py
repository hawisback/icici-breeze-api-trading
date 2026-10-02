"""Recorded outcome-free inventory of historical Breeze NIFTY research usage.

The source inventory was generated from the local Breeze historical database
without computing any predictor, target, correlation, P&L, threshold, or model
fit. It establishes that, within the database inventory window ending
2026-09-18, only one database session is not already covered by a known
inspected research window.

That single session is insufficient for the separately frozen forecasting
development design, which requires at least 80 complete sessions. This record
therefore closes the historical-corpus search for independent forecasting
development; it does not authorize scoring the uncovered session.
"""

HISTORICAL_CORPUS_INVENTORY_RECORDED_FINDINGS_V1 = {
    "research_type": "NIFTY_HISTORICAL_CORPUS_INVENTORY_RECORDED_FINDINGS_V1",
    "protocol_version": "NIFTY_HISTORICAL_CORPUS_INVENTORY_V1",
    "research_only": True,
    "outcome_free_inventory": True,
    "source": {
        "inventory_artifact_sha256": (
            "f860b5c2d78384c6268f04533f2bb7ccfc6d36f981ed196a9c43bc762540b7f5"
        ),
        "source_database_sha256": (
            "3e75de732077aa056c4027ed20f789059f11787fc7102c2de9a6a6ce04cbeee6"
        ),
        "inventory_window": ["2025-01-01", "2026-09-18"],
        "database_sessions": 421,
        "known_inspected_database_sessions": 420,
        "not_known_inspected_database_sessions": 1,
        "not_known_inspected_legacy_qa_complete_sessions": 1,
    },
    "sole_uncovered_session": {
        "date": "2026-05-18",
        "legacy_slice_rows": 75,
        "raw_5m_rows": 76,
        "legacy_75_bar_qa_complete": True,
        "legacy_qa_reasons": [],
        "instrument_id": "INST-NIFTY-FUT-2026-05-26",
        "volume_rows_present": 76,
        "open_interest_rows_present": 76,
    },
    "forecasting_development_context": {
        "required_complete_sessions": 80,
        "available_uncovered_complete_sessions": 1,
        "shortfall_sessions": 79,
        "historical_database_can_supply_required_independent_sample": False,
        "uncovered_session_must_not_be_used_as_standalone_model_validation": True,
        "genuinely_forward_development_data_required_if_project_activates": True,
    },
    "decision": (
        "HISTORICAL_BREEZE_CORPUS_EXHAUSTED_FOR_INDEPENDENT_"
        "FORECASTING_DEVELOPMENT_WITHIN_INVENTORY_WINDOW"
    ),
    "interpretation": (
        "Within the existing Breeze historical database through 2026-09-18, "
        "nearly all sessions are already covered by inspected research windows. "
        "Only 2026-05-18 is uncovered and QA-complete. One session cannot satisfy "
        "the frozen 80-session forecasting-development requirement, so no new "
        "independent forecasting sample can be carved from this historical "
        "database without reusing inspected evidence."
    ),
    "guardrails": {
        "predictor_computed": False,
        "target_computed": False,
        "correlation_computed": False,
        "pnl_scored": False,
        "threshold_selection": False,
        "model_fitting": False,
        "candidate_freeze": False,
        "blind_validation_opened": False,
        "implementation_allowed": False,
        "single_uncovered_session_not_promoted": True,
        "no_relabeling_inspected_data_as_new": True,
        "prospective_replication_unchanged": True,
        "forecasting_activation_lock_unchanged": True,
        "strategy_d_remains_paused": True,
    },
}
