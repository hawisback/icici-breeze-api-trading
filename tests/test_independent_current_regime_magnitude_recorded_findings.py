from services.historical.independent_current_regime_magnitude_recorded_findings import (
    CURRENT_REGIME_MAGNITUDE_RECORDED_FINDINGS_V1,
)


def test_recorded_magnitude_replication_is_sha_bound_and_passed():
    record = CURRENT_REGIME_MAGNITUDE_RECORDED_FINDINGS_V1
    assert record["source"]["findings_artifact_sha256"] == (
        "36a67516efba246a9e0c3704f237d00e49db4d82bde63ba6ebc7c13f5e730bb5"
    )
    assert record["source"]["market_artifact_sha256"] == (
        "fce2b8580119c80e58fbc9888af846cc6e42a74523e54577b9ec930b9e540811"
    )
    assert record["replication_gate"]["passed"] is True
    assert record["replication_gate"]["failures"] == []
    assert record["decision"] == "DESCRIPTIVE_MAGNITUDE_RELATIONSHIP_REPLICATED"


def test_recorded_magnitude_result_preserves_exact_statistics():
    result = CURRENT_REGIME_MAGNITUDE_RECORDED_FINDINGS_V1["results"]
    assert result["pooled_spearman"] == 0.24137782583899284
    assert result["block_spearman"] == {
        "block1": 0.20312730353301214,
        "block2": 0.10614439061137014,
        "block3": 0.09091952598660395,
    }
    assert result["session_cluster_bootstrap_95pct"] == [
        0.06682400757554846,
        0.3684552010492742,
    ]


def test_recorded_magnitude_result_cannot_be_promoted_or_tuned():
    guardrails = CURRENT_REGIME_MAGNITUDE_RECORDED_FINDINGS_V1["guardrails"]
    assert guardrails["no_followup_parameter_tuning"] is True
    assert guardrails["no_threshold_promotion"] is True
    assert guardrails["no_position_sizing_rule"] is True
    assert guardrails["no_directional_rule"] is True
    assert guardrails["no_candidate_freeze"] is True
    assert guardrails["no_blind_validation"] is True
    assert guardrails["no_implementation"] is True
    assert guardrails["strategy_d_remains_paused"] is True
