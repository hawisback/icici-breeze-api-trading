from services.historical.independent_options_behavior_blind_results import OPTIONS_BLIND_08


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
