"""Frozen calibration/profile study for the 5-minute NIFTY range forecast.

Question
--------
Is the transferred 5-minute range relationship broad and monotone across the
opening-range distribution, or concentrated in a small extreme subset?

Quartile cut points are learned only from the older 2022-2024 training sample
and then frozen. Those cut points are applied unchanged to 2025-2026. The fixed
5-minute log-linear model is also trained only on 2022-2024 and transferred
without refitting.

This is post-discovery calibration characterization, not blind validation or a
trading rule.
"""

PROTOCOL_VERSION = "NIFTY_BREEZE_5M_RANGE_CALIBRATION_V1"
CORPUS_ROLE = "POST_DISCOVERY_5M_RANGE_CALIBRATION_CHARACTERIZATION"

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
    "bars_per_session": 75,
    "minimum_complete_sessions": 400,
}

CHECKPOINT_MINUTES = 5
BINS = ["Q1", "Q2", "Q3", "Q4"]

QUARTILE_DEFINITION = {
    "cut_points_source": "2022_2024_training_first_5m_range_only",
    "application": "apply_frozen_training_cut_points_to_2025_2026",
    "no_transfer_rebinning": True,
}

MODEL = {
    "name": "single_feature_log_linear_ols",
    "equation": "log1p(remaining_range) = intercept + slope * log1p(first_5m_range)",
    "fit_on": "all_2022_2024_training_sessions_once",
    "refit_on_transfer_data": False,
}

PROFILE_METRICS = [
    "sessions",
    "predictor_mean_bps",
    "target_mean_bps",
    "target_median_bps",
    "forecast_mean_bps",
    "model_mae_bps",
    "fixed_training_median_baseline_mae_bps",
    "skill",
    "mean_signed_error_bps",
]

MONOTONICITY_GATE = {
    "pooled_target_means_strictly_increasing_q1_to_q4": True,
    "pooled_target_medians_strictly_increasing_q1_to_q4": True,
    "2025_target_means_strictly_increasing_q1_to_q4": True,
    "2026_target_means_strictly_increasing_q1_to_q4": True,
    "all_frozen_quartiles_nonempty_in_2025_and_2026": True,
}

GUARDRAILS = {
    "research_only": True,
    "post_discovery_characterization": True,
    "blind_validation": False,
    "transfer_refit": False,
    "transfer_rebinning": False,
    "feature_search": False,
    "model_family_search": False,
    "hyperparameter_search": False,
    "threshold_optimization": False,
    "directional_entry_exit_rule": False,
    "pnl_scored": False,
    "implementation_allowed": False,
    "no_rescue_on_same_samples": True,
    "strategy_d_remains_paused": True,
}
