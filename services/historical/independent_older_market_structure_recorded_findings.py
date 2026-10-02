"""Recorded findings from the frozen 2022-2024 Breeze replication."""

OLDER_MARKET_STRUCTURE_REPLICATION_RECORDED_FINDINGS_V1 = {
    "research_type": (
        "NIFTY_BREEZE_OLDER_MARKET_STRUCTURE_REPLICATION_RECORDED_FINDINGS_V1"
    ),
    "protocol_version": "NIFTY_BREEZE_OLDER_MARKET_STRUCTURE_REPLICATION_V1",
    "research_only": True,
    "provider": "BREEZE",
    "source": {
        "findings_artifact_sha256": (
            "6b60bc685da7b83886e12002ce14d7a5a214b58c83587303fbb12713635400d2"
        ),
        "source_market_sha256": (
            "dbc368f444d4d4956672b78b2d9e806f0940f526b4a013023e96d22592948ed5"
        ),
        "complete_sessions": 709,
        "first_date": "2022-02-01",
        "last_date": "2024-12-31",
    },
    "opening_range_vs_remaining_range": {
        "observations": 709,
        "pooled_spearman": 0.4770024656557355,
        "calendar_year_spearman": {
            "2022": 0.37126161509723155,
            "2023": 0.3611056750649398,
            "2024": 0.4025540936232298,
        },
        "session_cluster_bootstrap_95pct": [
            0.41527377133323673,
            0.5353024431579343,
        ],
        "replication_gate_passed": True,
        "failures": [],
    },
    "daily_range_persistence": {
        "observations": 708,
        "pooled_spearman": 0.4395066198376123,
        "calendar_year_spearman": {
            "2022": 0.4192614850274773,
            "2023": 0.40382798008232684,
            "2024": 0.19798961354068634,
        },
        "session_cluster_bootstrap_95pct": [
            0.37539265143319084,
            0.4994003732994226,
        ],
        "replication_gate_passed": True,
        "failures": [],
    },
    "decision": (
        "OLDER_HISTORICAL_BOTH_MARKET_STRUCTURE_RELATIONSHIPS_REPLICATED"
    ),
    "interpretation": (
        "Both preselected historical market-structure relationships replicate "
        "on the separate older 2022-2024 Breeze sample. Opening-range versus "
        "remaining-session range is the more stable relationship across the "
        "three calendar years; daily-range persistence also passes but weakens "
        "in 2024."
    ),
    "guardrails": {
        "feature_search": False,
        "posthoc_relationship_additions": False,
        "future_data_required": False,
        "pnl_scored": False,
        "implementation_allowed": False,
        "strategy_d_remains_paused": True,
    },
}
