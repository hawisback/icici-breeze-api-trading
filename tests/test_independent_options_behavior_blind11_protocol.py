from services.historical.independent_options_behavior_candidate_manifest_v2 import BLIND11_VALIDATION_PROTOCOL


def test_o2_blind11_final_protocol_is_frozen():
    protocol = BLIND11_VALIDATION_PROTOCOL
    assert protocol["candidate"] == "O2_DISTRIBUTED_OPTIONS_PARTICIPATION_EXPANSION"
    assert protocol["working_name"] == "OPTIONS_BLIND_11"
    assert protocol["frozen_window"]["start_date"] == "2025-12-24"
    assert protocol["frozen_window"]["end_date"] == "2026-01-07"
    assert protocol["frozen_window"]["breeze_near_month_expiries"] == ["2025-12-30", "2026-01-27"]
    assert protocol["frozen_window"]["option_expiries"] == ["2025-12-30", "2026-01-06", "2026-01-13"]
    assert "OPTIONS_BLIND_10" in protocol["ineligible_for_validation"]
    assert protocol["parameter_changes_before_scoring"] is False
    assert protocol["candidate_definition_changes_before_scoring"] is False
    assert protocol["control_matching_changes_before_scoring"] is False
    decision = protocol["prospective_decision_after_blind11"]
    assert decision["no_rescue_filters"] is True
    assert decision["no_primary_horizon_switch"] is True
    assert decision["no_hhi_threshold_change"] is True
    assert decision["no_dte_subset_selection"] is True
    assert decision["implementation_allowed_directly_from_blind11"] is False
