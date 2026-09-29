import numpy as np
import pandas as pd

from services.historical.independent_short_swing_opening_displacement_fade_findings import (
    _add_opening_features,
    _passes,
)
from services.historical.independent_short_swing_opening_displacement_fade_protocol import (
    GUARDRAILS,
    SEARCH_GRID,
    SOURCE,
)


def test_opening_displacement_uses_spot_and_resets_at_cohort_boundary():
    frame = pd.DataFrame({
        "cohort": ["cohort1", "cohort1", "cohort1", "cohort1", "cohort2", "cohort2"],
        "date": [
            "2026-01-01", "2026-01-01", "2026-01-02", "2026-01-02",
            "2025-01-01", "2025-01-02",
        ],
        "timestamp": pd.to_datetime([
            "2026-01-01 09:15", "2026-01-01 15:25",
            "2026-01-02 09:15", "2026-01-02 15:25",
            "2025-01-01 09:15", "2025-01-02 09:15",
        ]),
        "spot_close": [100.0, 102.0, 104.0, 105.0, 200.0, 198.0],
    })
    out = _add_opening_features(frame)
    c1_day2 = out[
        (out["cohort"] == "cohort1") & (out["date"] == "2026-01-02")
    ].iloc[0]
    assert np.isclose(c1_day2["opening_displacement_bps"], (104.0 / 102.0 - 1) * 10000)
    assert c1_day2["direction"] == -1.0

    c2_day1 = out[
        (out["cohort"] == "cohort2") & (out["date"] == "2025-01-01")
    ].iloc[0]
    assert pd.isna(c2_day1["opening_displacement_bps"])


def test_only_first_completed_bar_can_carry_opening_signal():
    frame = pd.DataFrame({
        "cohort": ["cohort1"] * 4,
        "date": ["2026-01-01", "2026-01-01", "2026-01-02", "2026-01-02"],
        "timestamp": pd.to_datetime([
            "2026-01-01 09:15", "2026-01-01 15:25",
            "2026-01-02 09:15", "2026-01-02 09:20",
        ]),
        "spot_close": [100.0, 101.0, 103.0, 104.0],
    })
    out = _add_opening_features(frame)
    day2 = out[out["date"] == "2026-01-02"]
    assert day2["is_first_completed_bar"].tolist() == [True, False]


def test_opening_displacement_grid_is_small_and_frozen():
    assert SOURCE["event_dataset_sha256"] == (
        "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f"
    )
    assert SEARCH_GRID["abs_opening_displacement_percentile_min"] == [25]
    assert SEARCH_GRID["options_fast_lead_filter"] == ["off"]
    assert SEARCH_GRID["fixed_exit_minutes"] == [5, 10, 15, 30]
    assert SEARCH_GRID["total_formulations"] == 4


def test_opening_displacement_gate_keeps_cross_cohort_requirement():
    summary = {
        "trades": 110,
        "trades_by_cohort": {"cohort1": 55, "cohort2": 55},
        "cohort1_mean_bps": 1.0,
        "cohort2_mean_bps": 0.5,
        "positive_chronological_blocks": 10,
        "session_cluster_bootstrap_mean_95pct_bps": [0.1, 1.2],
    }
    passed, failures = _passes(summary)
    assert passed is True
    assert failures == []
    summary["cohort1_mean_bps"] = -0.1
    passed, failures = _passes(summary)
    assert passed is False
    assert "cohort1_mean_not_positive" in failures


def test_opening_displacement_guardrails_forbid_rescue_and_blind():
    assert GUARDRAILS["blind_data_used"] is False
    assert GUARDRAILS["fresh_blind_data_must_not_be_loaded"] is True
    assert GUARDRAILS["first_completed_bar_only"] is True
    assert GUARDRAILS["no_session_anchor_filter"] is True
    assert GUARDRAILS["no_futures_spot_dislocation_filter"] is True
    assert GUARDRAILS["no_options_fast_lead_filter"] is True
    assert GUARDRAILS["no_time_search"] is True
    assert GUARDRAILS["no_DTE_filter"] is True
    assert GUARDRAILS["no_stop_target_search"] is True
    assert GUARDRAILS["no_post_hoc_rescue"] is True
    assert GUARDRAILS["strategy_d_remains_paused"] is True
