"""Frozen post-discovery temporal transfer study for the opening-range forecast.

A fixed opening-range model is fit once on the older 2022-2024 Breeze market
artifact and then applied without refitting to the later 2025-2026 Breeze
historical database.

The later 2025-2026 archive contributed to the original relationship discovery,
so this study is explicitly NOT blind validation. It tests temporal model
transfer and calibration stability only.
"""

PROTOCOL_VERSION = "NIFTY_BREEZE_OPENING_RANGE_TEMPORAL_TRANSFER_V1"
CORPUS_ROLE = "POST_DISCOVERY_TEMPORAL_TRANSFER_NOT_BLIND_VALIDATION"

TRAINING_SOURCE = {
    "market_artifact_sha256": (
        "dbc368f444d4d4956672b78b2d9e806f0940f526b4a013023e96d22592948ed5"
    ),
    "window": ["2022-02-01", "2024-12-31"],
    "provider": "BREEZE",
    "expected_complete_sessions": 709,
}

TRANSFER_SOURCE = {
    "historical_database_sha256": (
        "3e75de732077aa056c4027ed20f789059f11787fc7102c2de9a6a6ce04cbeee6"
    ),
    "window": ["2025-01-01", "2026-09-18"],
    "provider": "BREEZE",
    "common_session_slice": ["09:15", "15:25"],
    "bars_per_session": 75,
    "minimum_complete_sessions": 400,
}

PREDICTOR = "first_30m_high_low_range_bps"
TARGET = "post_09_40_remaining_session_high_low_range_bps"

MODEL = {
    "name": "single_feature_log_linear_ols",
    "fit_on": "all_2022_2024_training_sessions_once",
    "equation": "log1p(target) = intercept + slope * log1p(first_30m_range)",
    "refit_on_transfer_data": False,
}

BASELINE = {
    "name": "older_training_sample_median_target",
    "fit_on": "all_2022_2024_training_sessions_once",
    "refit_on_transfer_data": False,
}

METRICS = {
    "primary": "mean_absolute_error_bps",
    "skill": "1 - model_mae / baseline_mae",
    "secondary": "spearman_forecast_vs_target",
    "bootstrap_quantity": (
        "abs(target-fixed_training_median) - abs(target-fixed_model_forecast)"
    ),
}

BOOTSTRAP = {
    "draws": 10000,
    "seed": 20261001,
    "cluster": "session",
    "interval": "percentile_95",
}

TRANSFER_GATE = {
    "minimum_complete_sessions": 400,
    "model_mae_lower_than_baseline_in_2025": True,
    "model_mae_lower_than_baseline_in_2026": True,
    "model_pooled_mae_lower_than_baseline": True,
    "model_spearman_positive_in_2025": True,
    "model_spearman_positive_in_2026": True,
    "session_cluster_bootstrap_error_improvement_95pct_lower_must_be_positive": True,
}

GUARDRAILS = {
    "research_only": True,
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
}
