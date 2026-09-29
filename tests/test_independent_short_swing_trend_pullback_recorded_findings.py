from services.historical.independent_short_swing_trend_pullback_recorded_findings import (
    TREND_PULLBACK_DEVELOPMENT_FINDINGS_V1,
)


def test_trend_pullback_rejection_is_frozen():
    result = TREND_PULLBACK_DEVELOPMENT_FINDINGS_V1
    assert result["research_only"] is True
    assert result["candidate_frozen"] is False
    assert result["blind_data_used"] is False
    assert result["implementation_allowed"] is False
    assert result["decision"] == "REJECTED_NO_CANDIDATE_FREEZE"
    assert result["structural_result"]["formulations_passing_gate"] == 0
    assert result["structural_result"][
        "formulations_positive_mean_in_both_cohorts"
    ] == 0


def test_trend_pullback_cohort2_rejection_is_recorded():
    result = TREND_PULLBACK_DEVELOPMENT_FINDINGS_V1
    screen = result["structural_result"]
    assert screen["cohort2_positive_mean_formulations"] == "0/32"
    assert screen["formulations_reaching_9_of_14_positive_blocks"] == 0
    assert screen["formulations_with_positive_bootstrap_lower_bound"] == 0

    best = result["best_pooled_predeclared_formulation"]
    assert best["pooled_mean_bps"] > 0
    assert best["cohort1_mean_bps"] > 0
    assert best["cohort2_mean_bps"] < 0
    assert best["gross_minus_reference_cost_bps"] < 0
    assert best["session_cluster_bootstrap_mean_95pct_bps"][0] < 0


def test_trend_pullback_no_option_study_or_rescue():
    result = TREND_PULLBACK_DEVELOPMENT_FINDINGS_V1
    assert "do not open an exact-option" in result["guardrail"].lower()
    assert "do not rescue" in result["guardrail"].lower()
