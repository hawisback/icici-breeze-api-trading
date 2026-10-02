import pandas as pd

from services.historical.independent_short_swing_directional_efficiency_findings import (
    _non_overlapping,
    _passes_gate,
)
from services.historical.independent_short_swing_directional_efficiency_protocol import (
    GUARDRAILS,
    SEARCH_GRID,
    SOURCE,
    STRUCTURAL_GATE,
)


def test_directional_efficiency_protocol_identity_is_locked():
    assert SOURCE["event_dataset_sha256"] == (
        "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f"
    )
    assert SEARCH_GRID["abs_net_return_3_percentile_min"] == [60, 75]
    assert SEARCH_GRID["directional_efficiency_min"] == [0.60, 0.80]
    assert SEARCH_GRID["options_fast_lead_filter"] == [
        "off",
        "directional_agreement",
    ]
    assert SEARCH_GRID["fixed_exit_minutes"] == [5, 10, 15, 30]


def test_directional_efficiency_guardrails_remain_development_only():
    assert GUARDRAILS["research_only"] is True
    assert GUARDRAILS["blind_data_used"] is False
    assert GUARDRAILS["implementation_allowed"] is False
    assert GUARDRAILS["fresh_blind_data_must_not_be_loaded"] is True
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


def test_structural_gate_requires_stability_and_positive_bootstrap_lower_bound():
    summary = {
        "trades": 200,
        "trades_by_cohort": {"cohort1": 100, "cohort2": 100},
        "cohort1_mean_bps": 1.0,
        "cohort2_mean_bps": 0.5,
        "positive_chronological_blocks": 10,
        "session_cluster_bootstrap_mean_95pct_bps": [0.1, 1.4],
    }
    passed, failures = _passes_gate(summary)
    assert passed is True
    assert failures == []

    summary["session_cluster_bootstrap_mean_95pct_bps"] = [-0.01, 1.4]
    passed, failures = _passes_gate(summary)
    assert passed is False
    assert "bootstrap_lower_bound_not_positive" in failures


def test_structural_gate_constants_are_conservative():
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
