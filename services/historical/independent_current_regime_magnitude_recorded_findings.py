"""Recorded findings for the frozen current-regime magnitude replication.

The uploaded findings artifact passed the predeclared descriptive replication
gate. This record intentionally does not create a trading candidate, sizing
rule, threshold, or implementation authorization.
"""

CURRENT_REGIME_MAGNITUDE_RECORDED_FINDINGS_V1 = {
    "research_type": "NIFTY_CURRENT_REGIME_MAGNITUDE_RECORDED_FINDINGS_V1",
    "protocol_version": "NIFTY_CURRENT_REGIME_MAGNITUDE_REPLICATION_V1",
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "source": {
        "findings_artifact_sha256": (
            "36a67516efba246a9e0c3704f237d00e49db4d82bde63ba6ebc7c13f5e730bb5"
        ),
        "market_artifact_sha256": (
            "fce2b8580119c80e58fbc9888af846cc6e42a74523e54577b9ec930b9e540811"
        ),
        "sessions": 13,
        "market_rows": 1001,
        "scorable_events": 858,
    },
    "frozen_question": {
        "predictor": "trailing_30m_range_bps",
        "target": "next_30m_max_absolute_excursion_bps",
        "directional_claim": False,
        "pnl_scored": False,
        "threshold_selected": False,
    },
    "results": {
        "pooled_spearman": 0.24137782583899284,
        "block_spearman": {
            "block1": 0.20312730353301214,
            "block2": 0.10614439061137014,
            "block3": 0.09091952598660395,
        },
        "session_cluster_bootstrap_95pct": [
            0.06682400757554846,
            0.3684552010492742,
        ],
        "predictor_quartile_mean_future_excursion_bps": {
            "q1": 15.305038186525179,
            "q2": 13.55044883442712,
            "q3": 16.714451651003444,
            "q4": 17.744058155668984,
        },
    },
    "replication_gate": {
        "passed": True,
        "failures": [],
    },
    "decision": "DESCRIPTIVE_MAGNITUDE_RELATIONSHIP_REPLICATED",
    "interpretation": (
        "Recent 30-minute NIFTY futures range retained a positive rank relationship "
        "with the next 30-minute maximum absolute futures excursion in the new "
        "current-session-regime sample. All three chronological block correlations "
        "were positive and the session-cluster bootstrap lower bound exceeded zero. "
        "The quartile means were not perfectly monotonic because Q2 was below Q1, "
        "so this result supports a broad descriptive magnitude relationship rather "
        "than a threshold or bucket rule."
    ),
    "terminal_research_decision": (
        "Close this pilot. Do not tune windows, thresholds, quartiles, direction, "
        "filters, or P&L on these inspected sessions. The result may motivate a "
        "separate future forecasting project only if a new protocol and genuinely "
        "new development data are frozen first."
    ),
    "guardrails": {
        "no_followup_parameter_tuning": True,
        "no_threshold_promotion": True,
        "no_position_sizing_rule": True,
        "no_directional_rule": True,
        "no_pnl_claim": True,
        "no_candidate_freeze": True,
        "no_blind_validation": True,
        "no_implementation": True,
        "no_new_broker_or_trading_api": True,
        "strategy_d_remains_paused": True,
    },
}
