from services.historical.independent_short_swing_wick_rejection_recorded_findings import (
    WICK_REJECTION_DEVELOPMENT_FINDINGS_V1,
)


def test_wick_rejection_rejection_is_frozen():
    result = WICK_REJECTION_DEVELOPMENT_FINDINGS_V1
    assert result["research_only"] is True
    assert result["candidate_frozen"] is False
    assert result["blind_data_used"] is False
    assert result["implementation_allowed"] is False
    assert result["decision"] == "REJECTED_NO_CANDIDATE_FREEZE"
    assert result["structural_result"]["formulations_passing_gate"] == 0
    assert result["structural_result"][
        "formulations_with_positive_bootstrap_lower_bound"
    ] == 0


def test_wick_rejection_positive_pockets_are_not_promoted():
    result = WICK_REJECTION_DEVELOPMENT_FINDINGS_V1
    screen = result["structural_result"]
    assert screen["formulations_positive_mean_in_both_cohorts"] == 5
    assert screen["formulations_reaching_9_of_14_positive_blocks"] == 2

    closest = result["closest_to_structural_gate"]
    assert closest["cohort1_mean_bps"] > 0
    assert closest["cohort2_mean_bps"] > 0
    assert closest["positive_chronological_blocks"] == "9/14"
    assert closest["session_cluster_bootstrap_mean_95pct_bps"][0] < 0
    assert closest["gross_minus_reference_cost_bps"] < 0

    largest = result["largest_pooled_gross_mean"]
    assert largest["pooled_mean_bps"] > closest["pooled_mean_bps"]
    assert largest["positive_chronological_blocks"] == "8/14"
    assert largest["session_cluster_bootstrap_mean_95pct_bps"][0] < 0
    assert largest["gross_minus_reference_cost_bps"] < 0


def test_wick_rejection_no_option_study_or_rescue():
    result = WICK_REJECTION_DEVELOPMENT_FINDINGS_V1
    assert "do not open an exact-option" in result["guardrail"].lower()
    assert "do not rescue" in result["guardrail"].lower()
