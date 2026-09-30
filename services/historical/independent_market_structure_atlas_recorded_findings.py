"""Recorded findings from the frozen Breeze NIFTY market-structure atlas.

This record SHA-binds the exploratory atlas artifact and preserves the exact
predeclared-label outcomes. These findings are exploratory historical discovery,
not validation, a strategy candidate, or implementation authorization.
"""

MARKET_STRUCTURE_ATLAS_RECORDED_FINDINGS_V1 = {
    "research_type": "NIFTY_BREEZE_MARKET_STRUCTURE_ATLAS_RECORDED_FINDINGS_V1",
    "protocol_version": "NIFTY_BREEZE_MARKET_STRUCTURE_ATLAS_V1",
    "corpus_role": "EXPLORATORY_HISTORICAL_PATTERN_DISCOVERY_NOT_VALIDATION",
    "research_only": True,
    "exploratory": True,
    "source": {
        "findings_artifact_sha256": (
            "df787501fcf8513f5204e6c442912e0e71938664cff602d021623676d58db439"
        ),
        "source_database_sha256": (
            "3e75de732077aa056c4027ed20f789059f11787fc7102c2de9a6a6ce04cbeee6"
        ),
        "window": ["2025-01-01", "2026-09-18"],
        "accepted_complete_sessions": 412,
        "rejected_sessions": 1,
        "rejected_dates": ["2025-10-21"],
        "accepted_first_date": "2025-01-01",
        "accepted_last_date": "2026-09-07",
        "chronological_block_sizes": [69, 69, 69, 69, 68, 68],
    },
    "patterns_meeting_frozen_labels": [
        "opening_range_vs_remaining_range",
        "daily_range_persistence",
    ],
    "opening_range_vs_remaining_range": {
        "observations": 412,
        "pooled_spearman": 0.5086691594128394,
        "block_spearman": [
            0.2759956156375594,
            0.4818779685787358,
            0.23328461819510413,
            0.457983193277311,
            0.41168836126274,
            0.33858838798335683,
        ],
        "same_sign_blocks": 6,
        "bootstrap_95pct": [
            0.43403894017378525,
            0.5756271108979191,
        ],
        "frozen_exploratory_label": True,
    },
    "daily_range_persistence": {
        "observations": 411,
        "pooled_spearman": 0.39743756384484685,
        "block_spearman": [
            0.018742604114974994,
            0.11815856777493607,
            0.2506028498355864,
            0.3797588600657655,
            0.21231438714356604,
            0.4072603733251899,
        ],
        "same_sign_blocks": 6,
        "bootstrap_95pct": [
            0.3106894303727101,
            0.4771450729887842,
        ],
        "frozen_exploratory_label": True,
    },
    "intraday_volatility_seasonality": {
        "opening_mean_abs_return_bps": 6.457429068674871,
        "midday_mean_abs_return_bps": 4.2731233399493975,
        "late_mean_abs_return_bps": 4.170201219195573,
        "opening_to_midday_ratio": 1.5111731057010258,
        "late_to_midday_ratio": 0.9759140767616965,
        "opening_to_midday_block_ratios": [
            1.5566711106532722,
            1.7162334435993962,
            1.5453973343152057,
            1.5894836660828875,
            1.4661796739345325,
            1.1863649488976213,
        ],
        "combined_frozen_exploratory_label": False,
        "note": (
            "Opening volatility is descriptively elevated versus midday in all "
            "six blocks, but the frozen combined label also required an elevated "
            "late-session ratio and therefore did not pass."
        ),
    },
    "nonlabels": {
        "overnight_gap_vs_session_range": {
            "pooled_spearman": 0.12672569473769346,
            "same_sign_blocks": 4,
            "bootstrap_95pct": [
                0.024830608588658532,
                0.22544459029520442,
            ],
        },
        "weekday_range_seasonality": {
            "max_to_min_mean_ratio": 1.1120044537629536,
            "blocks_matching_pooled_max_category": 1,
        },
        "expiry_distance_range_structure": {
            "max_mean_category": "2_5",
            "min_mean_category": "0_1",
            "max_to_min_mean_ratio": 1.1831623818952892,
            "blocks_matching_pooled_max_category": 3,
        },
    },
    "interpretation": (
        "The strongest newly discovered structures in this historical Breeze "
        "sample are within-session volatility-state persistence from the first "
        "30 minutes into the rest of the session, and session-to-session range "
        "persistence. Both are positive across all six chronological blocks. "
        "These are exploratory historical findings only."
    ),
    "guardrails": {
        "blind_validation": False,
        "candidate_freeze": False,
        "implementation_allowed": False,
        "pnl_scored": False,
        "threshold_optimization": False,
        "directional_entry_exit_rule": False,
        "known_magnitude_thesis_retested": False,
        "strategy_d_remains_paused": True,
    },
}
