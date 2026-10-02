"""Frozen incremental-information study for replicated NIFTY range patterns.

Question
--------
After the first 30 minutes of a session are known, does the previous session's
realized range add forecast information for the remaining-session range beyond
the first-30-minute range alone?

This is same-history model development/characterization, not blind validation.
The model forms and folds are frozen before any combined-model outputs are
inspected.
"""

PROTOCOL_VERSION = "NIFTY_BREEZE_INCREMENTAL_RANGE_FORECAST_V1"
CORPUS_ROLE = "HISTORICAL_INCREMENTAL_INFORMATION_CHARACTERIZATION"

SOURCE = {
    "market_artifact_sha256": (
        "dbc368f444d4d4956672b78b2d9e806f0940f526b4a013023e96d22592948ed5"
    ),
    "window": ["2022-02-01", "2024-12-31"],
    "provider": "BREEZE",
    "minimum_complete_sessions": 600,
}

TARGET = "post_09_40_remaining_session_high_low_range_bps"
OPENING_PREDICTOR = "first_30m_high_low_range_bps"
PERSISTENCE_PREDICTOR = "previous_session_high_low_range_bps"

MODELS = {
    "opening_only": {
        "equation": (
            "log1p(target) = intercept + b_open * log1p(first_30m_range)"
        ),
        "features": [OPENING_PREDICTOR],
        "fit": "ordinary_least_squares_closed_form",
    },
    "opening_plus_previous_day": {
        "equation": (
            "log1p(target) = intercept + b_open * log1p(first_30m_range) + "
            "b_prev * log1p(previous_session_range)"
        ),
        "features": [OPENING_PREDICTOR, PERSISTENCE_PREDICTOR],
        "fit": "ordinary_least_squares_closed_form",
    },
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
    "incremental_error_improvement": (
        "abs(target-opening_only_forecast) - "
        "abs(target-opening_plus_previous_day_forecast)"
    ),
    "secondary": "spearman_forecast_vs_target",
}

BOOTSTRAP = {
    "draws": 10000,
    "seed": 20261001,
    "cluster": "session",
    "interval": "percentile_95",
}

INCREMENTAL_GATE = {
    "minimum_complete_sessions": 600,
    "combined_mae_lower_than_opening_only_in_both_folds": True,
    "combined_pooled_mae_lower_than_opening_only": True,
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
