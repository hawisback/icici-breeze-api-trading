from services.historical.independent_magnitude_forecasting_design_protocol import (
    ACTIVATION,
    BASELINE,
    BOOTSTRAP,
    DATA_POLICY,
    DEVELOPMENT_GATE,
    GUARDRAILS,
    MODEL,
    OUT_OF_SAMPLE_EVALUATION,
    PREDICTOR,
    PROTOCOL_VERSION,
    STATUS,
    TARGET,
)
from services.historical.independent_prospective_magnitude_protocol import (
    PREDICTOR as PROSPECTIVE_PREDICTOR,
    TARGET as PROSPECTIVE_TARGET,
    TERMINAL_OUTCOMES as PROSPECTIVE_TERMINAL_OUTCOMES,
)


def test_forecasting_design_is_frozen_but_not_activated():
    assert PROTOCOL_VERSION == "NIFTY_MAGNITUDE_FORECASTING_DESIGN_V1"
    assert STATUS == "DESIGN_FROZEN_NOT_ACTIVATED"
    assert ACTIVATION["data_collection_before_activation_allowed"] is False
    assert ACTIVATION["target_computation_before_activation_allowed"] is False
    assert ACTIVATION["model_fitting_before_activation_allowed"] is False
    assert ACTIVATION["model_scoring_before_activation_allowed"] is False
    assert ACTIVATION["if_prospective_fail"] == (
        "RETIRE_MAGNITUDE_THESIS_NO_FURTHER_TUNING_OR_RESCUE"
    )
    assert "ONLY_A_SEPARATE_FORECASTING_MODEL_PROJECT" in (
        PROSPECTIVE_TERMINAL_OUTCOMES["if_pass"]
    )


def test_forecasting_question_carries_forward_exact_predictor_and_target():
    assert PREDICTOR == PROSPECTIVE_PREDICTOR
    assert TARGET["name"] == PROSPECTIVE_TARGET["name"]
    assert TARGET["definition"] == PROSPECTIVE_TARGET["definition"]
    assert TARGET["future_bars"] == PROSPECTIVE_TARGET["future_bars"]


def test_model_is_single_feature_fixed_and_has_no_search_space():
    assert MODEL["name"] == "single_feature_log_linear_ols"
    assert MODEL["inputs"] == ["trailing_30m_range_bps"]
    assert MODEL["hyperparameters"] == {}
    assert MODEL["feature_selection"] is False
    assert MODEL["model_selection"] is False
    assert MODEL["hyperparameter_search"] is False
    assert BASELINE["name"] == "training_median_target"


def test_evaluation_is_strict_chronological_expanding_window():
    evaluation = OUT_OF_SAMPLE_EVALUATION
    assert evaluation["blocks"] == 4
    assert evaluation["random_shuffle"] is False
    assert evaluation["future_session_leakage_allowed"] is False
    assert evaluation["same_session_future_feature_leakage_allowed"] is False
    assert evaluation["minimum_complete_sessions"] == 80
    assert evaluation["minimum_sessions_per_block"] == 20
    assert evaluation["folds"] == [
        {"train_blocks": ["block1"], "test_block": "block2"},
        {"train_blocks": ["block1", "block2"], "test_block": "block3"},
        {
            "train_blocks": ["block1", "block2", "block3"],
            "test_block": "block4",
        },
    ]


def test_gate_requires_consistent_error_improvement_not_just_rank_signal():
    assert DEVELOPMENT_GATE == {
        "minimum_complete_sessions": 80,
        "pooled_model_mae_must_be_lower_than_baseline": True,
        "all_three_fold_model_mae_must_be_lower_than_baseline": True,
        "pooled_oos_spearman_must_be_positive": True,
        "session_cluster_bootstrap_error_improvement_95pct_lower_must_be_positive": True,
    }
    assert BOOTSTRAP == {
        "draws": 10000,
        "seed": 20261117,
        "cluster": "session",
        "interval": "percentile_95",
        "scope": "pooled_out_of_sample_sessions_only",
    }


def test_all_existing_inspected_and_robustness_samples_are_forbidden_for_model_scoring():
    forbidden = set(DATA_POLICY["forbidden_scoring_sources"])
    assert "NIFTY_RETROSPECTIVE_MAGNITUDE_ROBUSTNESS_V1" in forbidden
    assert "SHORT_SWING_DEVELOPMENT_V2" in forbidden
    assert "DEVELOPMENT_COHORT_4_V1" in forbidden
    assert "NIFTY_CURRENT_REGIME_MAGNITUDE_REPLICATION_V1" in forbidden
    assert "NIFTY_PROSPECTIVE_MAGNITUDE_REPLICATION_V1" in forbidden
    assert DATA_POLICY["genuinely_new_development_data_required"] is True


def test_design_cannot_promote_trading_or_reopen_strategy_d():
    assert GUARDRAILS["research_only"] is True
    assert GUARDRAILS["directional_claim"] is False
    assert GUARDRAILS["pnl_scored"] is False
    assert GUARDRAILS["threshold_selection"] is False
    assert GUARDRAILS["position_sizing_rule"] is False
    assert GUARDRAILS["model_family_search"] is False
    assert GUARDRAILS["feature_search"] is False
    assert GUARDRAILS["hyperparameter_search"] is False
    assert GUARDRAILS["pass_does_not_create_trading_candidate"] is True
    assert GUARDRAILS["pass_does_not_authorize_blind_validation"] is True
    assert GUARDRAILS["pass_does_not_authorize_implementation"] is True
    assert GUARDRAILS["strategy_d_remains_paused"] is True
