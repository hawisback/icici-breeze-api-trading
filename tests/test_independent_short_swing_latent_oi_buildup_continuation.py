import numpy as np
import pandas as pd

from services.historical.independent_short_swing_latent_oi_buildup_continuation_findings import (
    _passes,
    _thresholds,
)
from services.historical.independent_short_swing_latent_oi_buildup_continuation_protocol import (
    FROZEN_RULE,
    GUARDRAILS,
    SEARCH_GRID,
    SOURCE,
)


def test_latent_oi_thresholds_use_positive_oi_and_absolute_return_only():
    frame = pd.DataFrame({
        "oi_change_bps": [-5.0, 1.0, 2.0, 3.0, 4.0, 100.0],
        "current_abs_return_bps": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
    })
    thresholds = _thresholds(frame)
    assert np.isclose(
        thresholds["oi_change_bps_min"],
        np.percentile([1.0, 2.0, 3.0, 4.0, 100.0], 80),
    )
    assert np.isclose(
        thresholds["current_abs_return_bps_max"],
        np.percentile([1.0, 2.0, 3.0, 4.0, 5.0, 6.0], 50),
    )


def test_latent_oi_rule_and_grid_are_frozen():
    assert SOURCE["event_dataset_sha256"] == (
        "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f"
    )
    assert FROZEN_RULE["oi_change_percentile_min"] == 80
    assert FROZEN_RULE["current_abs_return_percentile_max"] == 50
    assert FROZEN_RULE["oi_change_must_be_positive"] is True
    assert FROZEN_RULE["current_return_must_be_nonzero"] is True
    assert SEARCH_GRID["options_fast_lead_filter"] == ["off"]
    assert SEARCH_GRID["volume_filter"] == ["off"]
    assert SEARCH_GRID["fixed_exit_minutes"] == [5, 10, 15, 30]
    assert SEARCH_GRID["total_formulations"] == 4


def test_latent_oi_gate_keeps_cross_cohort_stability_requirement():
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

    summary["cohort1_mean_bps"] = -0.1
    passed, failures = _passes(summary)
    assert passed is False
    assert "cohort1_mean_not_positive" in failures


def test_latent_oi_is_distinct_from_prior_impulse_oi_reversal():
    assert GUARDRAILS["no_large_price_impulse_filter"] is True
    assert GUARDRAILS["no_falling_OI_reversal_rule"] is True
    assert GUARDRAILS["no_volume_filter"] is True


def test_latent_oi_guardrails_keep_blind_closed_and_forbid_rescue():
    assert GUARDRAILS["blind_data_used"] is False
    assert GUARDRAILS["fresh_blind_data_must_not_be_loaded"] is True
    assert GUARDRAILS["no_options_fast_lead_filter"] is True
    assert GUARDRAILS["no_time_filter"] is True
    assert GUARDRAILS["no_DTE_filter"] is True
    assert GUARDRAILS["no_stop_target_search"] is True
    assert GUARDRAILS["no_post_hoc_rescue"] is True
    assert GUARDRAILS["strategy_d_remains_paused"] is True
