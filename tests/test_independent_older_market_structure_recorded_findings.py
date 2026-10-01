from services.historical.independent_older_market_structure_recorded_findings import (
    OLDER_MARKET_STRUCTURE_REPLICATION_RECORDED_FINDINGS_V1,
)


def test_older_replication_record_is_sha_bound():
    record = OLDER_MARKET_STRUCTURE_REPLICATION_RECORDED_FINDINGS_V1
    assert record["source"]["findings_artifact_sha256"] == (
        "6b60bc685da7b83886e12002ce14d7a5a214b58c83587303fbb12713635400d2"
    )
    assert record["source"]["source_market_sha256"] == (
        "dbc368f444d4d4956672b78b2d9e806f0940f526b4a013023e96d22592948ed5"
    )
    assert record["source"]["complete_sessions"] == 709
    assert record["source"]["first_date"] == "2022-02-01"
    assert record["source"]["last_date"] == "2024-12-31"


def test_both_preselected_relationships_replicated_across_all_years():
    record = OLDER_MARKET_STRUCTURE_REPLICATION_RECORDED_FINDINGS_V1
    for name in (
        "opening_range_vs_remaining_range",
        "daily_range_persistence",
    ):
        result = record[name]
        assert result["replication_gate_passed"] is True
        assert result["failures"] == []
        assert result["pooled_spearman"] > 0.0
        assert all(value > 0.0 for value in result["calendar_year_spearman"].values())
        assert result["session_cluster_bootstrap_95pct"][0] > 0.0

    assert record["decision"] == (
        "OLDER_HISTORICAL_BOTH_MARKET_STRUCTURE_RELATIONSHIPS_REPLICATED"
    )


def test_record_preserves_nontrading_research_status():
    guardrails = OLDER_MARKET_STRUCTURE_REPLICATION_RECORDED_FINDINGS_V1[
        "guardrails"
    ]
    assert guardrails["feature_search"] is False
    assert guardrails["posthoc_relationship_additions"] is False
    assert guardrails["pnl_scored"] is False
    assert guardrails["implementation_allowed"] is False
    assert guardrails["strategy_d_remains_paused"] is True
