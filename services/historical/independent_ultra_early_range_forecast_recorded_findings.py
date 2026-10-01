"""Recorded findings from the frozen ultra-early range forecast study."""

ULTRA_EARLY_RANGE_FORECAST_RECORDED_FINDINGS_V1 = {
    "research_type": "NIFTY_BREEZE_ULTRA_EARLY_RANGE_FORECAST_RECORDED_FINDINGS_V1",
    "protocol_version": "NIFTY_BREEZE_ULTRA_EARLY_RANGE_FORECAST_V1",
    "research_only": True,
    "source": {
        "findings_artifact_sha256": (
            "c57c94b9aceb984bbd6664bac5d0daaee7fbf5b7ace7777445e59954963b1ea5"
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
    "checkpoints_minutes": [5, 10],
    "stable_checkpoints_minutes": [5, 10],
    "earliest_stable_checkpoint_minutes": 5,
    "checkpoint_summary": {
        "5": {
            "pooled_model_mae_bps": 29.062960851631527,
            "pooled_baseline_mae_bps": 31.95326088674959,
            "pooled_mae_improvement_bps": 2.890300035118063,
            "pooled_skill": 0.09045399295433465,
            "pooled_spearman": 0.3992043025181015,
            "bootstrap_95pct": [
                1.3107200988203886,
                4.412285610374753,
            ],
            "2025_skill": 0.07026998285214725,
            "2025_spearman": 0.30512093190672007,
            "2026_skill": 0.11590231668774875,
            "2026_spearman": 0.5002377581278551,
            "gate_passed": True,
        },
        "10": {
            "pooled_model_mae_bps": 28.291860181016183,
            "pooled_baseline_mae_bps": 31.636735008389927,
            "pooled_mae_improvement_bps": 3.3448748273737436,
            "pooled_skill": 0.10572756090306723,
            "pooled_spearman": 0.4410449972657123,
            "bootstrap_95pct": [
                1.8052835130321891,
                4.8652126617649145,
            ],
            "2025_skill": 0.08420651259037382,
            "2025_spearman": 0.3613495701366834,
            "2026_skill": 0.13312228984500774,
            "2026_spearman": 0.5265594528891621,
            "gate_passed": True,
        },
    },
    "decision": "EARLIEST_STABLE_ULTRA_EARLY_RANGE_FORECAST_5M",
    "interpretation": (
        "Both predeclared ultra-early checkpoints pass the complete no-refit "
        "transfer gate. The first 5-minute candle is therefore the earliest "
        "observable checkpoint available in the 5-minute historical corpus with "
        "stable remaining-session range forecast utility. There is no earlier "
        "checkpoint to test at this data resolution."
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
