import numpy as np
import pandas as pd

from services.historical.independent_short_swing_wick_rejection_findings import (
    _add_wick_features,
    _non_overlapping,
    _passes_gate,
)
from services.historical.independent_short_swing_wick_rejection_protocol import (
    GUARDRAILS,
    HYPOTHESIS,
    SEARCH_GRID,
    SOURCE,
    STRUCTURAL_GATE,
)


def test_wick_rejection_protocol_identity_is_locked():
    assert SOURCE["event_dataset_sha256"] == (
        "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f"
    )
    assert SEARCH_GRID["current_bar_range_percentile_min"] == [50]
    assert SEARCH_GRID["dominant_wick_fraction_min"] == [0.45, 0.60]
    assert SEARCH_GRID["body_fraction_max"] == [0.25, 0.40]
    assert SEARCH_GRID["options_fast_lead_filter"] == [
        "off",
        "reversal_direction_agreement",
    ]
    assert SEARCH_GRID["fixed_exit_minutes"] == [5, 10, 15, 30]
    assert HYPOTHESIS["fixed_wick_dominance_ratio_min"] == 1.50


def test_wick_measurements_and_signal_direction_are_exact():
    frame = pd.DataFrame(
        {
            "futures_open": [100.0, 100.0],
            "futures_high": [110.0, 104.0],
            "futures_low": [98.0, 90.0],
            "futures_close": [102.0, 98.0],
        }
    )
    out = _add_wick_features(frame)

    upper = out.iloc[0]
    assert upper["direction"] == -1.0
    assert np.isclose(upper["upper_wick_points"], 8.0)
    assert np.isclose(upper["lower_wick_points"], 2.0)
    assert np.isclose(upper["dominant_wick_fraction"], 8.0 / 12.0)
    assert np.isclose(upper["body_fraction"], 2.0 / 12.0)
    assert np.isclose(upper["wick_dominance_ratio"], 4.0)

    lower = out.iloc[1]
    assert lower["direction"] == 1.0
    assert np.isclose(lower["upper_wick_points"], 4.0)
    assert np.isclose(lower["lower_wick_points"], 8.0)
    assert np.isclose(lower["dominant_wick_fraction"], 8.0 / 14.0)
    assert np.isclose(lower["body_fraction"], 2.0 / 14.0)
    assert np.isclose(lower["wick_dominance_ratio"], 2.0)


def test_wick_rejection_guardrails_keep_study_distinct():
    assert GUARDRAILS["research_only"] is True
    assert GUARDRAILS["blind_data_used"] is False
    assert GUARDRAILS["implementation_allowed"] is False
    assert GUARDRAILS["fresh_blind_data_must_not_be_loaded"] is True
    assert GUARDRAILS["prior_breakout_filters_forbidden"] is True
    assert GUARDRAILS["pullback_filters_forbidden"] is True
    assert GUARDRAILS["directional_efficiency_filters_forbidden"] is True
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
