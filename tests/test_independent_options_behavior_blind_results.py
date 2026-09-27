from services.historical.independent_options_behavior_blind_results import (
    OPTIONS_BLIND_08,
    OPTIONS_BLIND_09,
    OPTIONS_BLIND_08_09_COMBINED_EVALUATION,
    OPTIONS_BLIND_10_O2,
)


def test_options_blind_08_records_support_without_promotion():
    result = OPTIONS_BLIND_08
    assert result["candidate_id"] == "O1_LATENT_OPTIONS_PARTICIPATION_EXPANSION"
    assert result["scoring"]["threshold_source"] == "frozen DEVELOPMENT_CORPUS only"
    assert result["scoring"]["threshold_changes"] is False
    assert result["scoring"]["candidate_definition_changes"] is False
    assert result["primary_30m"]["scorable_episodes"] == 15
    assert result["primary_30m"]["pre_specified_direction_matched_mean"] is True
    assert result["primary_30m"]["pre_specified_direction_matched_median"] is True
    assert result["candidate_status_after_blind"] == "FROZEN_RESEARCH_ONLY_FIRST_BLIND_SUPPORT"
    assert result["implementation_allowed"] is False
    assert result["retune_from_blind_allowed"] is False


def test_options_blind_09_records_sparse_second_support_without_promotion():
    result = OPTIONS_BLIND_09
    assert result["scoring"]["threshold_source"] == "frozen DEVELOPMENT_CORPUS only"
    assert result["scoring"]["threshold_changes"] is False
    assert result["scoring"]["candidate_definition_changes"] is False
    assert result["primary_30m"]["scorable_episodes"] == 7
    assert result["primary_30m"]["sessions_with_episodes"] == 3
    assert result["primary_30m"]["pre_specified_direction_matched_mean"] is True
    assert result["primary_30m"]["pre_specified_direction_matched_median"] is True
    assert result["post_blind_evaluation_diagnostics"]["leave_one_session_out_30m"][
        "mean_lift_positive_for_all_10_omissions"
    ] is False
    assert result["implementation_allowed"] is False
    assert result["retune_from_blind_allowed"] is False


def test_combined_blind_evaluation_preserves_research_only_status():
    combined = OPTIONS_BLIND_08_09_COMBINED_EVALUATION
    assert combined["primary_30m"]["scorable_episodes"] == 22
    assert combined["primary_30m"]["positive_frozen_primary_blocks_mean"] == "2/2"
    assert combined["primary_30m"]["positive_frozen_primary_blocks_median"] == "2/2"
    assert combined["composition_checks"]["mean_residual_after_matching_dte_and_time_bucket_bps"] < 1.0
    assert combined["implementation_allowed"] is False
    assert combined["retune_from_blind_allowed"] is False


def test_options_blind_10_records_o2_conflict_without_retuning():
    result = OPTIONS_BLIND_10_O2
    assert result["candidate_id"] == "O2_DISTRIBUTED_OPTIONS_PARTICIPATION_EXPANSION"
    assert result["scoring"]["development_reproduction"]["primary_matched_episodes"] == 67
    assert result["scoring"]["threshold_changes"] is False
    assert result["scoring"]["candidate_definition_changes"] is False
    assert result["scoring"]["control_matching_changes"] is False
    assert result["primary_30m"]["matched_scorable_episodes"] == 10
    assert result["primary_30m"]["mean_matched_residual_bps"] < 1.0
    assert result["primary_30m"]["median_matched_residual_bps"] < 0.0
    assert result["candidate_status_after_blind"] == "FROZEN_RESEARCH_ONLY_FIRST_FRESH_BLIND_WEAK_OR_CONFLICTING"
    assert result["implementation_allowed"] is False
    assert result["retune_from_blind_allowed"] is False
