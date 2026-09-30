from services.historical.independent_historical_corpus_inventory_recorded_findings import (
    HISTORICAL_CORPUS_INVENTORY_RECORDED_FINDINGS_V1,
)


def test_inventory_record_is_sha_bound_and_exact():
    record = HISTORICAL_CORPUS_INVENTORY_RECORDED_FINDINGS_V1
    source = record["source"]

    assert source["inventory_artifact_sha256"] == (
        "f860b5c2d78384c6268f04533f2bb7ccfc6d36f981ed196a9c43bc762540b7f5"
    )
    assert source["source_database_sha256"] == (
        "3e75de732077aa056c4027ed20f789059f11787fc7102c2de9a6a6ce04cbeee6"
    )
    assert source["inventory_window"] == ["2025-01-01", "2026-09-18"]
    assert source["database_sessions"] == 421
    assert source["known_inspected_database_sessions"] == 420
    assert source["not_known_inspected_database_sessions"] == 1
    assert source["not_known_inspected_legacy_qa_complete_sessions"] == 1


def test_inventory_record_pins_the_single_uncovered_session():
    session = HISTORICAL_CORPUS_INVENTORY_RECORDED_FINDINGS_V1[
        "sole_uncovered_session"
    ]

    assert session == {
        "date": "2026-05-18",
        "legacy_slice_rows": 75,
        "raw_5m_rows": 76,
        "legacy_75_bar_qa_complete": True,
        "legacy_qa_reasons": [],
        "instrument_id": "INST-NIFTY-FUT-2026-05-26",
        "volume_rows_present": 76,
        "open_interest_rows_present": 76,
    }


def test_inventory_record_closes_historical_search_for_forecasting_development():
    record = HISTORICAL_CORPUS_INVENTORY_RECORDED_FINDINGS_V1
    context = record["forecasting_development_context"]

    assert context["required_complete_sessions"] == 80
    assert context["available_uncovered_complete_sessions"] == 1
    assert context["shortfall_sessions"] == 79
    assert context["historical_database_can_supply_required_independent_sample"] is False
    assert context["uncovered_session_must_not_be_used_as_standalone_model_validation"] is True
    assert context["genuinely_forward_development_data_required_if_project_activates"] is True
    assert record["decision"] == (
        "HISTORICAL_BREEZE_CORPUS_EXHAUSTED_FOR_INDEPENDENT_"
        "FORECASTING_DEVELOPMENT_WITHIN_INVENTORY_WINDOW"
    )


def test_inventory_record_preserves_research_firewalls():
    guardrails = HISTORICAL_CORPUS_INVENTORY_RECORDED_FINDINGS_V1["guardrails"]

    assert guardrails["predictor_computed"] is False
    assert guardrails["target_computed"] is False
    assert guardrails["correlation_computed"] is False
    assert guardrails["pnl_scored"] is False
    assert guardrails["threshold_selection"] is False
    assert guardrails["model_fitting"] is False
    assert guardrails["candidate_freeze"] is False
    assert guardrails["blind_validation_opened"] is False
    assert guardrails["implementation_allowed"] is False
    assert guardrails["single_uncovered_session_not_promoted"] is True
    assert guardrails["no_relabeling_inspected_data_as_new"] is True
    assert guardrails["prospective_replication_unchanged"] is True
    assert guardrails["forecasting_activation_lock_unchanged"] is True
    assert guardrails["strategy_d_remains_paused"] is True
