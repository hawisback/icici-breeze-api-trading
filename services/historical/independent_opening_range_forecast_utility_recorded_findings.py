"""Recorded findings from the frozen opening-range forecast utility study."""

OPENING_RANGE_FORECAST_UTILITY_RECORDED_FINDINGS_V1 = {
    "research_type": (
        "NIFTY_BREEZE_OPENING_RANGE_FORECAST_UTILITY_RECORDED_FINDINGS_V1"
    ),
    "protocol_version": "NIFTY_BREEZE_OPENING_RANGE_FORECAST_UTILITY_V1",
    "research_only": True,
    "source": {
        "findings_artifact_sha256": (
            "255253ead81149626cf8773bba6d9942fa70f96f9b62818f0829f8b2348405f8"
        ),
        "source_market_sha256": (
            "dbc368f444d4d4956672b78b2d9e806f0940f526b4a013023e96d22592948ed5"
        ),
        "source_complete_sessions": 709,
        "usable_sessions": 709,
    },
    "folds": [
        {
            "name": "train_2022_test_2023",
            "train_sessions": 220,
            "test_sessions": 243,
            "baseline_median_bps": 96.50071705084969,
            "model_mae_bps": 25.73478073091258,
            "baseline_mae_bps": 36.4461849878114,
            "skill": 0.2938964465137025,
            "model_mae_lower": True,
            "model_spearman": 0.3611056750649398,
        },
        {
            "name": "train_2022_2023_test_2024",
            "train_sessions": 463,
            "test_sessions": 246,
            "baseline_median_bps": 77.08479327259946,
            "model_mae_bps": 33.26666627534859,
            "baseline_mae_bps": 36.64525364999756,
            "skill": 0.09219713436610888,
            "model_mae_lower": True,
            "model_spearman": 0.4025540936232298,
        },
    ],
    "pooled_oos": {
        "sessions": 489,
        "model_mae_bps": 29.52382744651843,
        "baseline_mae_bps": 36.54632995897253,
        "mae_improvement_bps": 7.0225025124541,
        "skill": 0.1921534260851273,
        "model_spearman": 0.2695173714663378,
        "bootstrap_error_improvement_95pct": [
            5.633271426891652,
            8.47231250339339,
        ],
    },
    "utility_gate": {
        "passed": True,
        "failures": [],
    },
    "decision": "OPENING_RANGE_FORECAST_BEATS_UNCONDITIONAL_MEDIAN_BASELINE",
    "interpretation": (
        "The fixed opening-range-only forecast improves remaining-session range "
        "MAE versus the training-sample median baseline in both chronological "
        "out-of-sample folds. Pooled skill is positive and the session-bootstrap "
        "error-improvement interval is entirely above zero."
    ),
    "guardrails": {
        "same_history_characterization": True,
        "blind_validation": False,
        "feature_search": False,
        "model_family_search": False,
        "hyperparameter_search": False,
        "threshold_optimization": False,
        "directional_entry_exit_rule": False,
        "pnl_scored": False,
        "implementation_allowed": False,
        "no_rescue_on_same_sample": True,
        "strategy_d_remains_paused": True,
    },
}
