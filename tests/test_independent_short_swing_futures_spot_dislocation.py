import numpy as np
import pandas as pd

from services.historical.independent_short_swing_futures_spot_dislocation_findings import (
    _add_dislocation_features,
    _non_overlapping,
    _passes_gate,
)
from services.historical.independent_short_swing_futures_spot_dislocation_protocol import (
    GUARDRAILS,
    SEARCH_GRID,
    SOURCE,
    STRUCTURAL_GATE,
)


def test_futures_spot_dislocation_protocol_identity_is_locked():
    assert SOURCE["event_dataset_sha256"] == (
        "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f"
    )
    assert SEARCH_GRID["abs_return_dislocation_percentile_min"] == [80, 90]
    assert SEARCH_GRID["options_fast_lead_filter"] == [
        "off",
        "reversion_direction_agreement",
    ]
    assert SEARCH_GRID["fixed_exit_minutes"] == [5, 10, 15, 30]


def test_dislocation_measurement_and_signal_direction_are_exact():
    frame = pd.DataFrame(
        {
            "date": ["2026-09-01", "2026-09-01", "2026-09-01"],
            "spot_close": [100.0, 101.0, 100.0],
            "futures_return_bps": [np.nan, 150.0, -50.0],
        }
    )
    out = _add_dislocation_features(frame)

    second = out.iloc[1]
    expected_spot_2 = (101.0 / 100.0 - 1.0) * 10000.0
    expected_dislocation_2 = 150.0 - expected_spot_2
    assert np.isclose(second["spot_return_bps"], expected_spot_2)
    assert np.isclose(
        second["return_dislocation_bps"], expected_dislocation_2
    )
    assert second["direction"] == -np.sign(expected_dislocation_2)

    third = out.iloc[2]
    expected_spot_3 = (100.0 / 101.0 - 1.0) * 10000.0
    expected_dislocation_3 = -50.0 - expected_spot_3
    assert np.isclose(third["spot_return_bps"], expected_spot_3)
    assert np.isclose(
        third["return_dislocation_bps"], expected_dislocation_3
    )
    assert third["direction"] == -np.sign(expected_dislocation_3)


def test_futures_spot_guardrails_keep_study_distinct():
    assert GUARDRAILS["research_only"] is True
    assert GUARDRAILS["blind_data_used"] is False
    assert GUARDRAILS["implementation_allowed"] is False
    assert GUARDRAILS["fresh_blind_data_must_not_be_loaded"] is True
    assert GUARDRAILS["absolute_basis_filters_forbidden"] is True
    assert GUARDRAILS["breakout_filters_forbidden"] is True
    assert GUARDRAILS["pullback_filters_forbidden"] is True
    assert GUARDRAILS["wick_filters_forbidden"] is True
    assert GUARDRAILS["volume_filters_forbidden"] is True
    assert GUARDRAILS["oi_filters_forbidden"] is True
    assert GUARDRAILS["post_hoc_rescue_forbidden"] is True
    assert GUARDRAILS["strategy_d_remains_paused"] is True


def test_non_overlapping_accepts_signal_at_prior_fixed_exit():
    frame = pd.DataFrame(
        {
            "date": ["2026-09-01"] * 3,
            "entry_timestamp": pd.to_datetime(
                [
                    "2026-09-01 09:20:00",
                    "2026-09-01 09:25:00",
                    "2026-09-01 09:30:00",
                ]
            ),
        }
    )
    selected = _non_overlapping(frame, horizon_minutes=10)
    assert list(selected["entry_timestamp"].dt.strftime("%H:%M")) == [
        "09:20",
        "09:30",
    ]


def test_structural_gate_requires_cross_cohort_stability():
    summary = {
        "trades": 200,
        "trades_by_cohort": {"cohort1": 100, "cohort2": 100},
        "cohort1_mean_bps": 1.0,
        "cohort2_mean_bps": 0.7,
        "positive_chronological_blocks": 10,
        "session_cluster_bootstrap_mean_95pct_bps": [0.1, 1.5],
    }
    passed, failures = _passes_gate(summary)
    assert passed is True
    assert failures == []

    summary["cohort2_mean_bps"] = -0.01
    passed, failures = _passes_gate(summary)
    assert passed is False
    assert "cohort2_mean_not_positive" in failures


def test_structural_gate_constants_remain_conservative():
    assert STRUCTURAL_GATE["minimum_pooled_trades"] == 100
    assert STRUCTURAL_GATE["minimum_trades_per_cohort"] == 40
    assert (
        STRUCTURAL_GATE["minimum_positive_chronological_blocks_out_of_14"]
        == 9
    )
    assert (
        STRUCTURAL_GATE[
            "require_pooled_session_cluster_bootstrap_95pct_lower_bound_gt_zero"
        ]
        is True
    )
