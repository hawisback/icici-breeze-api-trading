"""Design-only protocol for a future NIFTY magnitude forecasting project.

This file freezes the modeling question and evaluation mechanics before any
forecasting-model development sample is selected, collected, or scored.

It deliberately does NOT activate a forecasting project today. The already
frozen prospective magnitude replication controls whether this project may
begin. If that prospective study fails, the magnitude thesis is retired and
this design remains unused.

If the prospective study passes, a separate source-data protocol must freeze a
GENUINELY NEW Breeze-only development sample before any target is computed or
model is fit. No currently inspected or robustness sample may be reused for
forecast-model scoring or selection.

Frozen forecasting question
----------------------------
Can a fixed, single-feature continuous model using only trailing 30-minute
NIFTY futures range forecast the next 30-minute maximum absolute excursion
better than an unconditional training-sample median forecast on genuinely new
development data?

This remains non-directional and non-trading. It defines no entry, exit,
position size, threshold, P&L rule, options feature, VIX feature, or execution
path.
"""
from __future__ import annotations

PROTOCOL_VERSION = "NIFTY_MAGNITUDE_FORECASTING_DESIGN_V1"
STATUS = "DESIGN_FROZEN_NOT_ACTIVATED"

ACTIVATION = {
    "requires_completed_prospective_protocol": (
        "NIFTY_PROSPECTIVE_MAGNITUDE_REPLICATION_V1"
    ),
    "requires_pass_decision": (
        "PROSPECTIVE_DESCRIPTIVE_MAGNITUDE_RELATIONSHIP_REPLICATED"
    ),
    "if_prospective_fail": (
        "RETIRE_MAGNITUDE_THESIS_NO_FURTHER_TUNING_OR_RESCUE"
    ),
    "data_collection_before_activation_allowed": False,
    "target_computation_before_activation_allowed": False,
    "model_fitting_before_activation_allowed": False,
    "model_scoring_before_activation_allowed": False,
}

QUESTION = (
    "Does a fixed one-feature continuous forecast based only on trailing "
    "30-minute NIFTY futures range reduce next-30-minute absolute-excursion "
    "forecast error versus a training-median baseline on genuinely new "
    "development data?"
)

PREDICTOR = {
    "name": "trailing_30m_range_bps",
    "definition": (
        "(max high - min low) over current+prior five 5m bars, divided by "
        "current 5m close, times 10000"
    ),
    "bars": 6,
}

TARGET = {
    "name": "next_30m_max_absolute_excursion_bps",
    "definition": (
        "max(next six 5m highs - current close, current close - next six 5m lows) "
        "divided by current close, times 10000"
    ),
    "future_bars": 6,
    "nonnegative": True,
}

MODEL = {
    "name": "single_feature_log_linear_ols",
    "inputs": ["trailing_30m_range_bps"],
    "fit_equation": (
        "log1p(target) = intercept + slope * log1p(trailing_30m_range_bps)"
    ),
    "fit_method": "ordinary_least_squares_closed_form",
    "hyperparameters": {},
    "prediction": (
        "max(0, expm1(intercept + slope * "
        "log1p(trailing_30m_range_bps)))"
    ),
    "feature_selection": False,
    "model_selection": False,
    "hyperparameter_search": False,
}

BASELINE = {
    "name": "training_median_target",
    "prediction": (
        "median next_30m_max_absolute_excursion_bps in the training sessions "
        "for that fold"
    ),
    "reason": "absolute-error baseline fixed before development scoring",
}

OUT_OF_SAMPLE_EVALUATION = {
    "session_order": "strict_chronological",
    "blocks": 4,
    "block_construction": (
        "split all frozen QA-eligible development sessions into four contiguous "
        "chronological blocks whose sizes differ by at most one; earlier blocks "
        "receive any remainder"
    ),
    "folds": [
        {
            "train_blocks": ["block1"],
            "test_block": "block2",
        },
        {
            "train_blocks": ["block1", "block2"],
            "test_block": "block3",
        },
        {
            "train_blocks": ["block1", "block2", "block3"],
            "test_block": "block4",
        },
    ],
    "random_shuffle": False,
    "future_session_leakage_allowed": False,
    "same_session_future_feature_leakage_allowed": False,
    "minimum_complete_sessions": 80,
    "minimum_sessions_per_block": 20,
}

METRICS = {
    "primary": "mean_absolute_error_bps",
    "skill_definition": (
        "1 - model_pooled_oos_mae_bps / baseline_pooled_oos_mae_bps"
    ),
    "fold_skill_definition": (
        "1 - model_fold_mae_bps / baseline_fold_mae_bps"
    ),
    "secondary": "pooled_oos_spearman_forecast_vs_target",
    "bootstrap_quantity": (
        "mean(|target-baseline_forecast| - |target-model_forecast|) in bps; "
        "positive values favor the model"
    ),
}

BOOTSTRAP = {
    "draws": 10000,
    "seed": 20261117,
    "cluster": "session",
    "interval": "percentile_95",
    "scope": "pooled_out_of_sample_sessions_only",
}

DEVELOPMENT_GATE = {
    "minimum_complete_sessions": 80,
    "pooled_model_mae_must_be_lower_than_baseline": True,
    "all_three_fold_model_mae_must_be_lower_than_baseline": True,
    "pooled_oos_spearman_must_be_positive": True,
    "session_cluster_bootstrap_error_improvement_95pct_lower_must_be_positive": True,
}

DATA_POLICY = {
    "provider": "BREEZE",
    "genuinely_new_development_data_required": True,
    "source_dates_must_be_frozen_before_collection_or_scoring": True,
    "source_contract_schedule_must_be_frozen_before_collection": True,
    "session_shape_must_be_frozen_in_source_protocol": True,
    "source_artifact_sha256_must_be_recorded": True,
    "forbidden_scoring_sources": [
        "NIFTY_RETROSPECTIVE_MAGNITUDE_ROBUSTNESS_V1",
        "SHORT_SWING_DEVELOPMENT_V2",
        "DEVELOPMENT_COHORT_4_V1",
        "NIFTY_CURRENT_REGIME_MAGNITUDE_REPLICATION_V1",
        "NIFTY_PROSPECTIVE_MAGNITUDE_REPLICATION_V1",
    ],
    "no_currently_inspected_sample_may_become_model_validation": True,
}

TERMINAL_OUTCOMES = {
    "if_development_gate_fails": (
        "FORECASTING_MODEL_DEVELOPMENT_FAILED_NO_RESCUE_ON_SAME_SAMPLE"
    ),
    "if_development_gate_passes": (
        "FORECASTING_MODEL_DEVELOPMENT_PASSED_BUT_NO_TRADING_PROMOTION; "
        "ANY_VALIDATION_REQUIRES_A_SEPARATELY_FROZEN_FUTURE_SAMPLE"
    ),
}

GUARDRAILS = {
    "research_only": True,
    "design_only_until_activation": True,
    "directional_claim": False,
    "pnl_scored": False,
    "threshold_selection": False,
    "quartile_analysis": False,
    "position_sizing_rule": False,
    "options_used": False,
    "vix_used": False,
    "volume_filter": False,
    "oi_filter": False,
    "time_of_day_filter": False,
    "dte_filter": False,
    "model_family_search": False,
    "feature_search": False,
    "hyperparameter_search": False,
    "no_rescue_after_development_results": True,
    "pass_does_not_create_trading_candidate": True,
    "pass_does_not_authorize_blind_validation": True,
    "pass_does_not_authorize_implementation": True,
    "new_broker_or_trading_api_used": False,
    "strategy_d_remains_paused": True,
}
