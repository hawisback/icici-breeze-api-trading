"""Frozen baseline-utility study for the replicated opening-range forecast.

Question
--------
Does the already-fixed opening-range-only log-linear forecast improve
out-of-sample remaining-session range MAE versus a training-sample median
baseline?

The predictor, target, model form, and chronological folds were already fixed
before this study. This protocol adds only the unconditional median baseline and
a predeclared utility gate. No feature/model search is allowed.
"""

PROTOCOL_VERSION = "NIFTY_BREEZE_OPENING_RANGE_FORECAST_UTILITY_V1"
CORPUS_ROLE = "HISTORICAL_FORECAST_UTILITY_CHARACTERIZATION"

SOURCE = {
    "market_artifact_sha256": (
        "dbc368f444d4d4956672b78b2d9e806f0940f526b4a013023e96d22592948ed5"
    ),
    "window": ["2022-02-01", "2024-12-31"],
    "provider": "BREEZE",
    "minimum_complete_sessions": 600,
}

PREDICTOR = "first_30m_high_low_range_bps"
TARGET = "post_09_40_remaining_session_high_low_range_bps"

MODEL = {
    "name": "single_feature_log_linear_ols",
    "equation": "log1p(target) = intercept + slope * log1p(first_30m_range)",
    "fit": "ordinary_least_squares_closed_form",
    "prediction": "max(0, expm1(intercept + slope * log1p(first_30m_range)))",
}

BASELINE = {
    "name": "training_sample_median_target",
    "prediction": "median(target) over training sessions in each fold",
    "reason": "primary loss is absolute error",
}

FOLDS = [
    {
        "name": "train_2022_test_2023",
        "train_years": [2022],
        "test_year": 2023,
    },
    {
        "name": "train_2022_2023_test_2024",
        "train_years": [2022, 2023],
        "test_year": 2024,
    },
]

METRICS = {
    "primary": "mean_absolute_error_bps",
    "skill": "1 - model_mae / baseline_mae",
    "secondary": "spearman_forecast_vs_target",
    "bootstrap_quantity": (
        "abs(target-baseline_forecast) - abs(target-model_forecast)"
    ),
}

BOOTSTRAP = {
    "draws": 10000,
    "seed": 20261001,
    "cluster": "session",
    "interval": "percentile_95",
}

UTILITY_GATE = {
    "minimum_complete_sessions": 600,
    "model_mae_lower_than_baseline_in_both_folds": True,
    "model_pooled_mae_lower_than_baseline": True,
    "model_spearman_positive_in_both_folds": True,
    "session_cluster_bootstrap_error_improvement_95pct_lower_must_be_positive": True,
}

GUARDRAILS = {
    "research_only": True,
    "same_history_characterization": True,
    "blind_validation": False,
    "feature_search": False,
    "model_family_search": False,
    "hyperparameter_search": False,
    "threshold_optimization": False,
    "directional_entry_exit_rule": False,
    "pnl_scored": False,
    "position_sizing_rule": False,
    "implementation_allowed": False,
    "no_rescue_on_same_sample": True,
    "strategy_d_remains_paused": True,
}
