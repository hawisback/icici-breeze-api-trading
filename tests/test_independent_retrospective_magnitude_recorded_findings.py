from services.historical.independent_retrospective_magnitude_recorded_findings import (
    RETROSPECTIVE_MAGNITUDE_RECORDED_FINDINGS_V1,
)


def test_recorded_retrospective_result_is_sha_bound_and_passed():
    record = RETROSPECTIVE_MAGNITUDE_RECORDED_FINDINGS_V1
    assert record["source"]["findings_artifact_sha256"] == (
        "126e71a4e64b163fd63b552120920154e6f38694844e2d7b697d7d665bd04098"
    )
    assert record["source"]["source_database_sha256"] == (
        "3e75de732077aa056c4027ed20f789059f11787fc7102c2de9a6a6ce04cbeee6"
    )
    assert record["source"]["complete_sessions"] == 89
    assert record["source"]["scorable_events"] == 5696
    assert record["source"]["rejected_sessions"] == 0
    assert record["replication_gate"] == {"passed": True, "failures": []}
    assert record["decision"] == "RETROSPECTIVE_MAGNITUDE_RELATIONSHIP_ROBUST"


def test_recorded_retrospective_result_preserves_exact_statistics():
    result = RETROSPECTIVE_MAGNITUDE_RECORDED_FINDINGS_V1["results"]
    assert result["pooled_spearman"] == 0.5143377386952068
    assert result["block_spearman"] == {
        "block1": 0.40555050185499303,
        "block2": 0.4755622009465391,
        "block3": 0.6271285117509019,
    }
    assert result["session_cluster_bootstrap_95pct"] == [
        0.4261179711899666,
        0.5877584159627744,
    ]
    assert RETROSPECTIVE_MAGNITUDE_RECORDED_FINDINGS_V1[
        "chronological_block_sizes"
    ] == {
        "block1": 30,
        "block2": 30,
        "block3": 29,
    }


def test_recorded_retrospective_result_remains_non_trading_and_non_prospective():
    record = RETROSPECTIVE_MAGNITUDE_RECORDED_FINDINGS_V1
    assert record["retrospective"] is True
    assert record["prospective_validation"] is False
    assert record["candidate_frozen"] is False
    assert record["implementation_allowed"] is False

    guardrails = record["guardrails"]
    assert guardrails["no_followup_parameter_tuning"] is True
    assert guardrails["no_threshold_promotion"] is True
    assert guardrails["no_position_sizing_rule"] is True
    assert guardrails["no_directional_rule"] is True
    assert guardrails["no_pnl_claim"] is True
    assert guardrails["no_candidate_freeze"] is True
    assert guardrails["no_blind_validation"] is True
    assert guardrails["no_implementation"] is True
    assert guardrails["does_not_replace_prospective_replication"] is True
    assert guardrails["strategy_d_remains_paused"] is True
