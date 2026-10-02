import numpy as np
import pandas as pd

from services.historical.independent_short_swing_low_volume_price_shock_reversal_findings import (
    _passes,
    _return_threshold,
)
from services.historical.independent_short_swing_low_volume_price_shock_reversal_protocol import (
    FROZEN_RULE,
    GUARDRAILS,
    SEARCH_GRID,
    SOURCE,
)


def test_return_threshold_is_frozen_p70_of_absolute_current_returns():
    frame = pd.DataFrame({
        "current_abs_return_bps": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, np.nan],
    })
    threshold = _return_threshold(frame)
    assert np.isclose(threshold, np.percentile([1, 2, 3, 4, 5, 6], 70))


def test_low_volume_shock_rule_and_grid_are_frozen():
    assert SOURCE["event_dataset_sha256"] == (
        "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f"
    )
    assert FROZEN_RULE["current_abs_return_percentile_min"] == 70
    assert FROZEN_RULE["volume_vs_prior3_mean_max"] == 1.0
    assert FROZEN_RULE["current_return_must_be_nonzero"] is True
    assert SEARCH_GRID["options_fast_lead_filter"] == ["off"]
    assert SEARCH_GRID["OI_filter"] == ["off"]
    assert SEARCH_GRID["fixed_exit_minutes"] == [5, 10, 15, 30]
    assert SEARCH_GRID["total_formulations"] == 4


def test_structural_gate_keeps_cross_cohort_stability_requirement():
    summary = {
        "trades": 120,
        "trades_by_cohort": {"cohort1": 60, "cohort2": 60},
        "cohort1_mean_bps": 0.8,
        "cohort2_mean_bps": 0.4,
        "positive_chronological_blocks": 10,
        "session_cluster_bootstrap_mean_95pct_bps": [0.1, 1.1],
    }
    passed, failures = _passes(summary)
    assert passed is True
    assert failures == []

    summary["cohort2_mean_bps"] = -0.1
    passed, failures = _passes(summary)
    assert passed is False
    assert "cohort2_mean_not_positive" in failures


def test_low_volume_shock_is_not_prior_mean_reversion_or_impulse_oi_rule():
    assert GUARDRAILS["no_close_location_filter"] is True
    assert GUARDRAILS["no_recent_range_filter"] is True
    assert GUARDRAILS["no_OI_filter"] is True
    assert GUARDRAILS["no_high_volume_impulse_rule"] is True


def test_guardrails_keep_blind_closed_and_forbid_rescue():
    assert GUARDRAILS["blind_data_used"] is False
    assert GUARDRAILS["fresh_blind_data_must_not_be_loaded"] is True
    assert GUARDRAILS["no_options_fast_lead_filter"] is True
    assert GUARDRAILS["no_time_filter"] is True
    assert GUARDRAILS["no_DTE_filter"] is True
    assert GUARDRAILS["no_stop_target_search"] is True
    assert GUARDRAILS["no_post_hoc_rescue"] is True
    assert GUARDRAILS["strategy_d_remains_paused"] is True
