"""Frozen ultra-early NIFTY range forecast checkpoint study.

Follow-up to NIFTY_BREEZE_RANGE_FORECAST_CHECKPOINTS_V1 after its earliest
predeclared stable checkpoint was 15 minutes.

This is a separate post-discovery characterization. Only 5-minute and
10-minute checkpoints are evaluated; the completed 15-120 minute study is not
modified.

Each checkpoint fits the same single-feature log-linear model on 2022-2024 and
transfers it without refitting to 2025-2026 against a fixed training-median
remaining-range baseline.
"""

PROTOCOL_VERSION = "NIFTY_BREEZE_ULTRA_EARLY_RANGE_FORECAST_V1"
CORPUS_ROLE = "POST_DISCOVERY_ULTRA_EARLY_TIMING_CHARACTERIZATION"

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

CHECKPOINTS_MINUTES = [5, 10]

MODEL = {
    "name": "single_feature_log_linear_ols",
    "equation": "log1p(remaining_range) = intercept + slope * log1p(range_so_far)",
    "fit_on": "all_2022_2024_training_sessions_once_per_checkpoint",
    "refit_on_transfer_data": False,
}

BASELINE = {
    "name": "training_sample_median_remaining_range",
    "fit_on": "all_2022_2024_training_sessions_once_per_checkpoint",
    "refit_on_transfer_data": False,
}

BOOTSTRAP = {
    "draws": 10000,
    "seed": 20261001,
    "cluster": "session",
    "interval": "percentile_95",
}

CHECKPOINT_GATE = {
    "model_mae_lower_than_baseline_in_2025": True,
    "model_mae_lower_than_baseline_in_2026": True,
    "model_pooled_mae_lower_than_baseline": True,
    "model_spearman_positive_in_2025": True,
    "model_spearman_positive_in_2026": True,
    "bootstrap_error_improvement_95pct_lower_must_be_positive": True,
}

INTERPRETATION = {
    "report_both_checkpoints": True,
    "earliest_stable_checkpoint": (
        "earliest of 5 or 10 minutes satisfying the complete checkpoint gate"
    ),
    "no_posthoc_checkpoint_additions_or_removals": True,
}

GUARDRAILS = {
    "research_only": True,
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
}
