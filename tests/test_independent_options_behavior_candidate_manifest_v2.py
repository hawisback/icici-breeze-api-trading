from services.historical.independent_options_behavior_candidate_manifest_v2 import (
    BLIND_VALIDATION_PROTOCOL,
    DEVELOPMENT_CORPUS,
    O2_DISTRIBUTED_OPTIONS_PARTICIPATION_EXPANSION,
    OPTIONS_BEHAVIOR_VERSION,
)


def test_o2_manifest_is_frozen_research_only():
    assert OPTIONS_BEHAVIOR_VERSION == "OPTIONS_BEHAVIOR_V2"
    assert DEVELOPMENT_CORPUS["sha256"] == "5d640d90e03f4cfb6bfe7c1ba2b2a0c44525b8ab01b4ea4ac62abd350dd9991c"
    candidate = O2_DISTRIBUTED_OPTIONS_PARTICIPATION_EXPANSION
    assert candidate["status"] == "FROZEN_FOR_FRESH_BLIND_VALIDATION"
    assert candidate["directional_claim"] is False
    assert candidate["implementation_allowed"] is False
    assert candidate["frozen_threshold_protocol"]["threshold_source"] == "DEVELOPMENT_CORPUS only"
    assert candidate["development_results"]["primary_30m"]["positive_mean_residual_blocks"] == "8/8"
    assert candidate["development_results"]["primary_30m"]["matched_scorable_episodes"] == 67


def test_o2_blind_protocol_forbids_reuse_and_retuning():
    protocol = BLIND_VALIDATION_PROTOCOL
    assert protocol["candidate"] == "O2_DISTRIBUTED_OPTIONS_PARTICIPATION_EXPANSION"
    assert protocol["working_name"] == "OPTIONS_BLIND_10"
    assert "OPTIONS_BLIND_08" in protocol["ineligible_for_validation"]
    assert "OPTIONS_BLIND_09" in protocol["ineligible_for_validation"]
    assert protocol["exact_window_must_be_frozen_before_collection"] is True
    assert protocol["parameter_changes_before_scoring"] is False
    assert protocol["mine_blind_for_new_rules"] is False
    assert protocol["thresholds_from_blind"] is False
    assert protocol["directional_score"] is None
    assert protocol["production_implementation_from_freeze"] is False
