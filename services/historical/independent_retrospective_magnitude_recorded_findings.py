"""Recorded findings for frozen retrospective NIFTY magnitude robustness.

The uploaded findings artifact passed the predeclared retrospective robustness
gate on the fixed 2025-01-01 through 2025-05-14 Breeze window. This record
preserves the exact reported statistics and explicitly does not convert the
result into prospective validation, a trading candidate, threshold, sizing
rule, or implementation authorization.
"""

RETROSPECTIVE_MAGNITUDE_RECORDED_FINDINGS_V1 = {
    "research_type": "NIFTY_RETROSPECTIVE_MAGNITUDE_RECORDED_FINDINGS_V1",
    "protocol_version": "NIFTY_RETROSPECTIVE_MAGNITUDE_ROBUSTNESS_V1",
    "corpus_role": "RETROSPECTIVE_ROBUSTNESS_NOT_VALIDATION",
    "research_only": True,
    "retrospective": True,
    "prospective_validation": False,
    "candidate_frozen": False,
    "implementation_allowed": False,
    "source": {
        "findings_artifact_sha256": (
            "126e71a4e64b163fd63b552120920154e6f38694844e2d7b697d7d665bd04098"
        ),
        "source_database_sha256": (
            "3e75de732077aa056c4027ed20f789059f11787fc7102c2de9a6a6ce04cbeee6"
        ),
        "provider": "BREEZE",
        "window": ["2025-01-01", "2025-05-14"],
        "complete_sessions": 89,
        "scorable_events": 5696,
        "rejected_sessions": 0,
        "weekday_dates_with_no_breeze_rows": [
            "2025-02-26",
            "2025-03-14",
            "2025-03-31",
            "2025-04-10",
            "2025-04-14",
            "2025-04-18",
            "2025-05-01",
        ],
    },
    "session_semantics": {
        "start": "09:15",
        "last_bar": "15:25",
        "bars_per_session": 75,
        "optional_15_30_bar_ignored": True,
        "scorable_events_per_session": 64,
    },
    "frozen_question": {
        "predictor": "trailing_30m_range_bps",
        "target": "next_30m_max_absolute_excursion_bps",
        "directional_claim": False,
        "pnl_scored": False,
        "threshold_selected": False,
        "quartile_analysis": False,
    },
    "chronological_block_sizes": {
        "block1": 30,
        "block2": 30,
        "block3": 29,
    },
    "results": {
        "pooled_spearman": 0.5143377386952068,
        "block_spearman": {
            "block1": 0.40555050185499303,
            "block2": 0.4755622009465391,
            "block3": 0.6271285117509019,
        },
        "session_cluster_bootstrap_95pct": [
            0.4261179711899666,
            0.5877584159627744,
        ],
    },
    "replication_gate": {
        "passed": True,
        "failures": [],
    },
    "decision": "RETROSPECTIVE_MAGNITUDE_RELATIONSHIP_ROBUST",
    "interpretation": (
        "On this fixed older Breeze sample, recent 30-minute NIFTY futures range "
        "showed a positive rank relationship with the next 30-minute maximum "
        "absolute excursion. The relationship was positive in all three "
        "chronological blocks and the session-cluster bootstrap lower bound "
        "remained above zero. This materially strengthens retrospective evidence "
        "that the magnitude relationship is not unique to the September 2026 "
        "current-regime pilot, but it remains historical robustness evidence only."
    ),
    "terminal_research_decision": (
        "Close this retrospective robustness test as passed. Do not tune the "
        "window, thresholds, buckets, direction, filters, P&L, or sizing on this "
        "inspected sample. Preserve the already-frozen prospective October-November "
        "replication as the separate forward test."
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
        "does_not_replace_prospective_replication": True,
        "no_new_broker_or_trading_api": True,
        "strategy_d_remains_paused": True,
    },
}
