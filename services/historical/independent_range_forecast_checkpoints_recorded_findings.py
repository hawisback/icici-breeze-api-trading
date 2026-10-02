"""Recorded findings from the frozen NIFTY range forecast checkpoint study."""

RANGE_FORECAST_CHECKPOINTS_RECORDED_FINDINGS_V1 = {
    "research_type": "NIFTY_BREEZE_RANGE_FORECAST_CHECKPOINTS_RECORDED_FINDINGS_V1",
    "protocol_version": "NIFTY_BREEZE_RANGE_FORECAST_CHECKPOINTS_V1",
    "research_only": True,
    "source": {
        "findings_artifact_sha256": (
            "23db0ef4eb21d92f497e88bcf8784dcb150e21eb88442b5b9a64478033cd2476"
        ),
        "training_market_sha256": (
            "dbc368f444d4d4956672b78b2d9e806f0940f526b4a013023e96d22592948ed5"
        ),
        "transfer_database_sha256": (
            "3e75de732077aa056c4027ed20f789059f11787fc7102c2de9a6a6ce04cbeee6"
        ),
        "training_complete_sessions": 709,
        "transfer_complete_sessions": 412,
        "transfer_rejected_sessions": 1,
        "transfer_rejected_dates": ["2025-10-21"],
    },
    "checkpoints_minutes": [15, 30, 60, 90, 120],
    "stable_checkpoints_minutes": [15, 30, 60, 90, 120],
    "earliest_stable_checkpoint_minutes": 15,
    "checkpoint_summary": {
        "15": {
            "pooled_skill": 0.1122214616515631,
            "pooled_mae_improvement_bps": 3.5328518604983614,
            "pooled_spearman": 0.4691183341761266,
            "bootstrap_95pct": [
                2.0000929219149888,
                5.060714337927576,
            ],
            "gate_passed": True,
        },
        "30": {
            "pooled_skill": 0.1212217309325595,
            "pooled_mae_improvement_bps": 3.745350943060288,
            "pooled_spearman": 0.5086691594128394,
            "bootstrap_95pct": [
                2.2839053343772924,
                5.177333265437826,
            ],
            "gate_passed": True,
        },
        "60": {
            "pooled_skill": 0.136162624854547,
            "pooled_mae_improvement_bps": 4.071522631573316,
            "pooled_spearman": 0.5225978119177198,
            "bootstrap_95pct": [
                2.6596024719417346,
                5.468541408662983,
            ],
            "gate_passed": True,
        },
        "90": {
            "pooled_skill": 0.14326174115498103,
            "pooled_mae_improvement_bps": 4.151521507483142,
            "pooled_spearman": 0.5199817496799416,
            "bootstrap_95pct": [
                2.6589926030527886,
                5.574104879029715,
            ],
            "gate_passed": True,
        },
        "120": {
            "pooled_skill": 0.14025296067886528,
            "pooled_mae_improvement_bps": 3.848366195539228,
            "pooled_spearman": 0.517463493783206,
            "bootstrap_95pct": [
                2.4144900119656025,
                5.204839848468905,
            ],
            "gate_passed": True,
        },
    },
    "decision": "EARLIEST_STABLE_RANGE_FORECAST_CHECKPOINT_15M",
    "interpretation": (
        "Every predeclared checkpoint from 15 through 120 minutes shows stable "
        "post-discovery no-refit forecast utility on the 2025-2026 transfer "
        "sample. Fifteen minutes is the earliest tested stable checkpoint. "
        "Later checkpoints improve pooled skill only modestly, with the highest "
        "predeclared pooled skill at 90 minutes."
    ),
    "guardrails": {
        "post_discovery_characterization": True,
        "blind_validation": False,
        "transfer_refit": False,
        "feature_search": False,
        "model_family_search": False,
        "hyperparameter_search": False,
        "threshold_optimization": False,
        "directional_entry_exit_rule": False,
        "pnl_scored": False,
        "implementation_allowed": False,
        "no_rescue_on_same_samples": True,
        "strategy_d_remains_paused": True,
    },
}
