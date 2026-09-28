from services.historical.independent_short_swing_expansion_pause_findings import (
    EXPANSION_PAUSE_DEVELOPMENT_FINDINGS_V1,
)


def test_expansion_pause_rejection_preserves_guardrails():
    result = EXPANSION_PAUSE_DEVELOPMENT_FINDINGS_V1
    assert result["research_only"] is True
    assert result["candidate_frozen"] is False
    assert result["blind_data_used"] is False
    assert result["implementation_allowed"] is False
    assert result["decision"] == "REJECTED_NO_CANDIDATE_FREEZE"
    assert "do not consume fresh blind data" in result["guardrail"].lower()


def test_expansion_pause_grid_identity_is_locked():
    grid = EXPANSION_PAUSE_DEVELOPMENT_FINDINGS_V1["predeclared_grid"]
    assert grid["prior_3_abs_net_return_percentile_min"] == [70, 80]
    assert grid["pause_range_divided_by_prior_3_range_max"] == [0.33, 0.50]
    assert grid["options_fast_lead_filter"] == [
        "off",
        "directional_agreement",
    ]
    assert grid["fixed_exit_minutes"] == [5, 10, 15, 30]
    assert grid["total_formulations"] == 32


def test_best_structural_formulation_is_not_promoted():
    screen = EXPANSION_PAUSE_DEVELOPMENT_FINDINGS_V1["structural_screen"]
    best = screen["best_predeclared_formulation"]
    assert best["cohort1_mean_bps"] > 0
    assert best["cohort2_mean_bps"] > 0
    assert best["session_cluster_bootstrap_mean_95pct_bps"][0] < 0
    assert best["positive_chronological_block_means"] == "9/14"


def test_exact_option_implementations_are_negative_before_slippage():
    option = EXPANSION_PAUSE_DEVELOPMENT_FINDINGS_V1[
        "exact_option_implementation"
    ]
    assert option["ATM"]["zero_slippage"]["pooled_net_mean_points"] < 0
    assert (
        option["one_strike_ITM"]["zero_slippage"]["pooled_net_mean_points"]
        < 0
    )
