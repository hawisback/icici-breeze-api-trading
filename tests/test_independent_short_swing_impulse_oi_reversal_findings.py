from services.historical.independent_short_swing_impulse_oi_reversal_findings import (
    IMPULSE_OI_REVERSAL_DEVELOPMENT_FINDINGS_V1,
)


def test_impulse_oi_reversal_rejection_preserves_guardrails():
    result = IMPULSE_OI_REVERSAL_DEVELOPMENT_FINDINGS_V1
    assert result["research_only"] is True
    assert result["candidate_frozen"] is False
    assert result["blind_data_used"] is False
    assert result["implementation_allowed"] is False
    assert result["decision"] == "REJECTED_NO_CANDIDATE_FREEZE"
    assert "do not consume fresh blind data" in result["guardrail"].lower()


def test_impulse_oi_grid_identity_is_locked():
    grid = IMPULSE_OI_REVERSAL_DEVELOPMENT_FINDINGS_V1["predeclared_grid"]
    assert grid["current_abs_return_percentile_min"] == [80, 90]
    assert grid["volume_vs_prior3_mean_min"] == [1.2, 1.5]
    assert grid["options_fast_lead_filter"] == [
        "off",
        "agrees_with_reversal",
    ]
    assert grid["fixed_exit_minutes"] == [5, 10, 15, 30]
    assert grid["total_formulations"] == 32


def test_only_structural_pass_is_weak_in_cohort2():
    screen = IMPULSE_OI_REVERSAL_DEVELOPMENT_FINDINGS_V1["structural_screen"]
    assert screen["formulations_positive_in_both_cohorts"] == 1
    best = screen["only_cross_cohort_positive_formulation"]
    assert best["cohort1_mean_bps"] > 0
    assert best["cohort2_mean_bps"] > 0
    assert best["cohort2_mean_bps"] < 0.1
    assert best["session_cluster_bootstrap_mean_95pct_bps"][0] < 0


def test_oi_nonconfirmation_is_not_specific_and_options_fail_cohort2():
    result = IMPULSE_OI_REVERSAL_DEVELOPMENT_FINDINGS_V1
    control = result["oi_confirmation_control"]["best_control_formulation"]
    assert control["cohort1_mean_bps"] > 0
    assert control["cohort2_mean_bps"] > 0

    options = result["exact_option_implementation"]
    assert options["ATM"]["zero_slippage"]["cohort2_net_mean_points"] < 0
    assert (
        options["one_strike_ITM"]["zero_slippage"]["cohort2_net_mean_points"]
        < 0
    )
    assert options["ATM"]["plus_1_point_each_side"]["pooled_net_mean_points"] < 0
    assert (
        options["one_strike_ITM"]["plus_1_point_each_side"][
            "pooled_net_mean_points"
        ]
        < 0
    )
