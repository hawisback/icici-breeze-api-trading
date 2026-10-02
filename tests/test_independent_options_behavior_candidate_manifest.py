from services.historical.independent_options_behavior_candidate_manifest import (
    DEVELOPMENT_CORPUS,
    NEXT_BLIND_PROTOCOL,
    O1_LATENT_OPTIONS_PARTICIPATION_EXPANSION,
    OPTIONS_BEHAVIOR_VERSION,
)


def test_options_behavior_o1_freeze_is_research_only_and_non_directional():
    assert OPTIONS_BEHAVIOR_VERSION == "OPTIONS_BEHAVIOR_V1"
    assert DEVELOPMENT_CORPUS["sessions"] == 80
    assert DEVELOPMENT_CORPUS["contract_stitching"] is False
    assert len(DEVELOPMENT_CORPUS["sha256"]) == 64

    candidate = O1_LATENT_OPTIONS_PARTICIPATION_EXPANSION
    assert candidate["status"] == "FROZEN_FOR_BLIND_VALIDATION"
    assert candidate["directional_claim"] is False
    assert candidate["implementation_allowed"] is False
    assert candidate["development_results"]["primary_30m"]["positive_mean_lift_blocks"] == "8/8"
    assert candidate["development_results"]["primary_30m"]["positive_median_lift_blocks"] == "8/8"

    blind = NEXT_BLIND_PROTOCOL
    assert blind["candidate"] == candidate["candidate_id"]
    assert blind["sessions"] == 10
    assert blind["parameter_changes_before_scoring"] is False
    assert blind["mine_blind_for_new_rules"] is False
    assert blind["thresholds_from_blind"] is False
    assert blind["directional_score"] is None
    assert blind["production_implementation_from_freeze"] is False
