from services.historical.independent_short_swing_futures_spot_dislocation_options_protocol import (
    GUARDRAILS,
    OPTION_IMPLEMENTATION,
    PASS_CRITERIA,
    STRUCTURAL_RULE_FAMILY,
)
from services.historical.independent_short_swing_futures_spot_dislocation_recorded_findings import (
    FUTURES_SPOT_DISLOCATION_STRUCTURAL_FINDINGS_V1,
)


def test_structural_pass_is_recorded_without_freezing_candidate():
    result = FUTURES_SPOT_DISLOCATION_STRUCTURAL_FINDINGS_V1
    assert result["decision"] == "STRUCTURAL_PASS_OPTIONS_IMPLEMENTATION_PENDING"
    assert result["candidate_frozen"] is False
    assert result["blind_data_used"] is False
    assert len(result["structural_gate_passes"]) == 11
    assert result["robust_option_neighborhood"]["all_four_cells_pass_structural_gate"] is True


def test_option_neighborhood_is_frozen_not_single_best_cell():
    assert STRUCTURAL_RULE_FAMILY["abs_return_dislocation_percentile_min"] == [80, 90]
    assert STRUCTURAL_RULE_FAMILY["fixed_exit_minutes"] == [5, 10]
    assert STRUCTURAL_RULE_FAMILY["options_fast_lead_filter"] == "reversion_direction_agreement"
    assert OPTION_IMPLEMENTATION["strike_variants"] == ["ATM", "one_strike_ITM"]
    assert OPTION_IMPLEMENTATION["slippage_points_per_side"] == [0.0, 1.0, 2.0]


def test_option_pass_criteria_require_cost_and_neighborhood_robustness():
    assert PASS_CRITERIA["primary_slippage_points_per_side"] == 1.0
    assert PASS_CRITERIA["require_positive_net_mean_both_cohorts"] is True
    assert PASS_CRITERIA["require_positive_pooled_net_mean_at_2_points_per_side"] is True
    assert PASS_CRITERIA["minimum_positive_chronological_blocks_out_of_14_at_primary_slippage"] == 9
    assert PASS_CRITERIA["require_pooled_session_cluster_bootstrap_95pct_lower_bound_gt_zero_at_primary_slippage"] is True
    assert "three of the four" in PASS_CRITERIA["neighborhood_requirement"].lower()


def test_option_guardrails_keep_blind_data_and_rescue_closed():
    assert GUARDRAILS["research_only"] is True
    assert GUARDRAILS["blind_data_used"] is False
    assert GUARDRAILS["fresh_blind_data_must_not_be_loaded"] is True
    assert GUARDRAILS["no_other_thresholds"] is True
    assert GUARDRAILS["no_other_horizons"] is True
    assert GUARDRAILS["no_other_strikes"] is True
    assert GUARDRAILS["no_post_hoc_rescue"] is True
    assert GUARDRAILS["strategy_d_remains_paused"] is True
