import numpy as np
import pandas as pd

from services.historical.independent_short_swing_session_anchor_reversion_options_findings import (
    _atm_strike,
    _cost_points,
    _implementation_pass,
)
from services.historical.independent_short_swing_session_anchor_reversion_options_protocol import (
    GUARDRAILS,
    OPTION_IMPLEMENTATION,
    PASS_CRITERIA,
    SOURCE,
    STRUCTURAL_RULE,
)


def test_session_anchor_option_protocol_locks_only_structural_pass():
    assert STRUCTURAL_RULE["abs_anchor_deviation_percentile_min"] == 80
    assert np.isclose(
        STRUCTURAL_RULE["derived_abs_anchor_deviation_threshold_bps"],
        21.570703282817405,
    )
    assert STRUCTURAL_RULE["options_fast_lead_filter"] == "reversion_direction_agreement"
    assert STRUCTURAL_RULE["fixed_exit_minutes"] == 5


def test_atm_is_primary_and_itm_cannot_rescue():
    assert OPTION_IMPLEMENTATION["primary_strike_variant"] == "ATM"
    assert OPTION_IMPLEMENTATION["robustness_strike_variant"] == "one_strike_ITM"
    assert PASS_CRITERIA["primary_strike_variant"] == "ATM"
    assert PASS_CRITERIA["robustness_strike_is_non_rescuing"] is True


def test_option_source_hashes_are_frozen_inspected_development_data():
    assert SOURCE["event_dataset_sha256"] == (
        "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f"
    )
    assert SOURCE["cohort1_options_sha256"] == (
        "5d640d90e03f4cfb6bfe7c1ba2b2a0c44525b8ab01b4ea4ac62abd350dd9991c"
    )
    assert SOURCE["cohort2_options_sha256"] == (
        "296d66947da845f3489ad97efec2b0651c0559f7b4ff0d6c486ceba05a8c98cc"
    )
    assert SOURCE["blind_data_used"] is False


def test_atm_rounding_and_costs_are_deterministic():
    assert _atm_strike(25024.9) == 25000
    assert _atm_strike(25025.0) == 25050
    costs = _cost_points(
        np.array([100.0, 200.0]),
        np.array([110.0, 190.0]),
    )
    assert costs.shape == (2,)
    assert np.all(costs > 0)


def test_implementation_gate_requires_all_primary_conditions():
    summaries = {
        "1.0": {
            "cohort1_net_mean_points": 1.0,
            "cohort2_net_mean_points": 0.5,
            "positive_chronological_blocks": 10,
            "session_cluster_bootstrap_net_mean_95pct_points": [0.1, 2.0],
        },
        "2.0": {"pooled_net_mean_points": 0.2},
    }
    passed, failures = _implementation_pass(summaries)
    assert passed is True
    assert failures == []

    summaries["1.0"]["cohort2_net_mean_points"] = -0.1
    passed, failures = _implementation_pass(summaries)
    assert passed is False
    assert "primary_slippage_not_positive_both_cohorts" in failures


def test_option_guardrails_keep_blind_closed_and_forbid_rescue():
    assert GUARDRAILS["blind_data_used"] is False
    assert GUARDRAILS["fresh_blind_data_must_not_be_loaded"] is True
    assert GUARDRAILS["no_other_anchor_thresholds"] is True
    assert GUARDRAILS["no_other_horizons"] is True
    assert GUARDRAILS["no_other_strikes"] is True
    assert GUARDRAILS["no_DTE_filter"] is True
    assert GUARDRAILS["no_time_filter"] is True
    assert GUARDRAILS["no_stop_target_search"] is True
    assert GUARDRAILS["no_post_hoc_rescue"] is True
    assert GUARDRAILS["strategy_d_remains_paused"] is True
