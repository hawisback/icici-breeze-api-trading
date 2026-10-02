"""Recorded findings from the frozen opening-range temporal transfer study."""

OPENING_RANGE_TEMPORAL_TRANSFER_RECORDED_FINDINGS_V1 = {
    "research_type": (
        "NIFTY_BREEZE_OPENING_RANGE_TEMPORAL_TRANSFER_RECORDED_FINDINGS_V1"
    ),
    "protocol_version": "NIFTY_BREEZE_OPENING_RANGE_TEMPORAL_TRANSFER_V1",
    "research_only": True,
    "source": {
        "findings_artifact_sha256": (
            "149534c08739ca883050ac8a7b310d4caf2d8ae6a8d840810bfb12b5af0829da"
        ),
        "training_market_sha256": (
            "dbc368f444d4d4956672b78b2d9e806f0940f526b4a013023e96d22592948ed5"
        ),
        "transfer_database_sha256": (
            "3e75de732077aa056c4027ed20f789059f11787fc7102c2de9a6a6ce04cbeee6"
        ),
        "training_sessions": 709,
        "transfer_complete_sessions": 412,
        "transfer_rejected_sessions": 1,
        "transfer_rejected_dates": ["2025-10-21"],
    },
    "fixed_model": {
        "coefficients": [
            2.5348731316431006,
            0.5008455138396859,
        ],
        "refit_on_transfer_data": False,
    },
    "fixed_baseline": {
        "median_target_bps": 78.20154547614436,
        "refit_on_transfer_data": False,
    },
    "calendar_year_results": {
        "2025": {
            "sessions": 247,
            "model_mae_bps": 26.36856266775199,
            "baseline_mae_bps": 28.879699812872595,
            "mae_improvement_bps": 2.5111371451206033,
            "skill": 0.08695163597238331,
            "model_mae_lower": True,
            "model_spearman": 0.41879819836464,
        },
        "2026": {
            "sessions": 165,
            "model_mae_bps": 28.323146034087895,
            "baseline_mae_bps": 33.91607763224576,
            "mae_improvement_bps": 5.592931598157865,
            "skill": 0.16490502406564778,
            "model_mae_lower": True,
            "model_spearman": 0.6122592365025513,
        },
    },
    "pooled_transfer": {
        "sessions": 412,
        "model_mae_bps": 27.151344841163212,
        "baseline_mae_bps": 30.8966957842235,
        "mae_improvement_bps": 3.745350943060288,
        "skill": 0.1212217309325595,
        "model_spearman": 0.5086691594128394,
        "bootstrap_error_improvement_95pct": [
            2.2839053343772924,
            5.177333265437826,
        ],
    },
    "transfer_gate": {
        "passed": True,
        "failures": [],
    },
    "decision": "FIXED_OLDER_OPENING_RANGE_MODEL_TRANSFERS_TO_2025_2026",
    "interpretation": (
        "A fixed model fit only on the older 2022-2024 Breeze sample retains "
        "positive forecast utility in both 2025 and 2026 without refitting. "
        "The transfer sample is post-discovery and therefore is not blind "
        "validation, but the no-refit transfer materially strengthens evidence "
        "that the opening-range relationship is temporally stable."
    ),
    "guardrails": {
        "post_discovery_transfer": True,
        "blind_validation": False,
        "transfer_refit": False,
        "feature_search": False,
        "model_family_search": False,
        "hyperparameter_search": False,
        "threshold_optimization": False,
        "directional_entry_exit_rule": False,
        "pnl_scored": False,
        "implementation_allowed": False,
        "no_rescue_on_same_transfer_sample": True,
        "strategy_d_remains_paused": True,
    },
}
