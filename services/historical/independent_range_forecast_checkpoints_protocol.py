"""Frozen checkpoint study for NIFTY intraday range forecast timing.

Question
--------
At what predeclared elapsed-session checkpoints does the realized range so far
provide stable out-of-sample forecast utility for the remaining-session range?

Each checkpoint uses the same fixed single-feature log-linear model family:
log1p(remaining_range) = intercept + slope * log1p(range_so_far).

Training is 2022-02-01 through 2024-12-31. Each checkpoint model and its
training-sample median baseline are fit once on that older sample, then applied
without refitting to the 2025-2026 Breeze archive.

This is a frozen multi-checkpoint historical characterization. It is not blind
validation, a directional signal, or a trading rule.
"""

PROTOCOL_VERSION = "NIFTY_BREEZE_RANGE_FORECAST_CHECKPOINTS_V1"
CORPUS_ROLE = "POST_DISCOVERY_CHECKPOINT_TIMING_CHARACTERIZATION"

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

CHECKPOINTS_MINUTES = [15, 30, 60, 90, 120]

CHECKPOINT_DEFINITION = {
    "predictor": (
        "high-low range from 09:15 through the final 5m bar contained in the "
        "checkpoint, normalized by session open and expressed in bps"
    ),
    "target": (
        "high-low range over all 5m bars strictly after the checkpoint, "
        "normalized by session open and expressed in bps"
    ),
    "bars_per_minute_unit": 5,
}

MODEL = {
    "name": "single_feature_log_linear_ols",
    "equation": "log1p(target) = intercept + slope * log1p(range_so_far)",
    "fit_on": "all_2022_2024_training_sessions_once_per_checkpoint",
    "refit_on_transfer_data": False,
}

BASELINE = {
    "name": "training_sample_median_remaining_range",
    "fit_on": "all_2022_2024_training_sessions_once_per_checkpoint",
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

CHECKPOINT_GATE = {
    "model_mae_lower_than_baseline_in_2025": True,
    "model_mae_lower_than_baseline_in_2026": True,
    "model_pooled_mae_lower_than_baseline": True,
    "model_spearman_positive_in_2025": True,
    "model_spearman_positive_in_2026": True,
    "bootstrap_error_improvement_95pct_lower_must_be_positive": True,
}

INTERPRETATION = {
    "report_all_checkpoints": True,
    "earliest_stable_checkpoint": (
        "earliest predeclared checkpoint satisfying the complete checkpoint gate"
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
