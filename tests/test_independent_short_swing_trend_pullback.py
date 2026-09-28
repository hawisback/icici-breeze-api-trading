import numpy as np
import pandas as pd

from services.historical.independent_short_swing_trend_pullback_findings import (
    _add_pullback_features,
    _non_overlapping,
    _passes_gate,
)
from services.historical.independent_short_swing_trend_pullback_protocol import (
    GUARDRAILS,
    SEARCH_GRID,
    SOURCE,
    STRUCTURAL_GATE,
)


def test_trend_pullback_protocol_identity_is_locked():
    assert SOURCE["event_dataset_sha256"] == (
        "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f"
    )
    assert SEARCH_GRID["impulse_abs_return_percentile_min"] == [70, 80]
    assert SEARCH_GRID["pullback_fraction_min"] == [0.20]
    assert SEARCH_GRID["pullback_fraction_max"] == [0.50, 0.75]
    assert SEARCH_GRID["options_fast_lead_filter"] == [
        "off",
        "original_direction_agreement",
    ]
    assert SEARCH_GRID["fixed_exit_minutes"] == [5, 10, 15, 30]


def test_six_bar_impulse_pullback_features_are_exact():
    frame = pd.DataFrame(
        {
            "date": ["2026-09-01"] * 6,
            "futures_open": [100.0, 102.0, 104.0, 106.0, 105.0, 104.0],
            "futures_close": [102.0, 104.0, 106.0, 105.0, 104.0, 103.0],
        }
    )
    out = _add_pullback_features(frame)
    row = out.iloc[-1]
    assert np.isclose(row["impulse_return_bps"], 600.0)
    expected_pullback = (103.0 / 106.0 - 1.0) * 10000.0
    assert np.isclose(row["pullback_return_bps"], expected_pullback)
    assert row["countertrend_pullback"]
    assert row["direction"] == 1.0
    assert np.isclose(
        row["pullback_fraction"], abs(expected_pullback) / 600.0
    )


def test_trend_pullback_guardrails_keep_study_distinct():
    assert GUARDRAILS["research_only"] is True
    assert GUARDRAILS["blind_data_used"] is False
    assert GUARDRAILS["implementation_allowed"] is False
    assert GUARDRAILS["fresh_blind_data_must_not_be_loaded"] is True
    assert GUARDRAILS["directional_efficiency_filters_forbidden"] is True
    assert GUARDRAILS["breakout_filters_forbidden"] is True
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
