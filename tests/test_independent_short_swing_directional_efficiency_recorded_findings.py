from services.historical.independent_short_swing_directional_efficiency_recorded_findings import (
    DIRECTIONAL_EFFICIENCY_DEVELOPMENT_FINDINGS_V1,
)


def test_directional_efficiency_rejection_is_frozen():
    result = DIRECTIONAL_EFFICIENCY_DEVELOPMENT_FINDINGS_V1
    assert result["research_only"] is True
    assert result["candidate_frozen"] is False
    assert result["blind_data_used"] is False
    assert result["implementation_allowed"] is False
    assert result["decision"] == "REJECTED_NO_CANDIDATE_FREEZE"
    assert result["structural_result"]["formulations_passing_gate"] == 0
    assert result["structural_result"][
        "formulations_positive_mean_in_both_cohorts"
    ] == 0


def test_directional_efficiency_cohort_split_is_recorded():
    result = DIRECTIONAL_EFFICIENCY_DEVELOPMENT_FINDINGS_V1
    screen = result["structural_result"]
    assert screen["cohort1_positive_mean_formulations"] == "0/32"
    assert screen["cohort2_positive_mean_formulations"] == "31/32"
    best = result["best_pooled_predeclared_formulation"]
    assert best["cohort1_mean_bps"] < 0
    assert best["cohort2_mean_bps"] > 0
    assert best["session_cluster_bootstrap_mean_95pct_bps"][0] < 0


def test_low_efficiency_control_cannot_be_promoted():
    result = DIRECTIONAL_EFFICIENCY_DEVELOPMENT_FINDINGS_V1
    control = result["non_promotable_low_efficiency_control"]
    assert control["trades"] == 58
    assert control["trades_by_cohort"]["cohort2"] == 14
    assert control["gross_minus_reference_cost_bps"] < 0
    assert "cannot be promoted" in control["role"].lower()
    assert "do not rescue" in result["guardrail"].lower()
