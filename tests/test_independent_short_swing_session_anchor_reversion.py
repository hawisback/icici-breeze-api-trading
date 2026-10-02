import numpy as np
import pandas as pd

from services.historical.independent_short_swing_futures_spot_dislocation_option_recorded_findings import (
    FUTURES_SPOT_DISLOCATION_OPTION_FINDINGS_V1,
)
from services.historical.independent_short_swing_session_anchor_reversion_findings import (
    _add_anchor_features,
    _passes,
)
from services.historical.independent_short_swing_session_anchor_reversion_protocol import (
    GUARDRAILS,
    SEARCH_GRID,
    SOURCE,
)


def test_dislocation_option_failure_is_recorded_without_candidate_freeze():
    result = FUTURES_SPOT_DISLOCATION_OPTION_FINDINGS_V1
    assert result["decision"] == "REJECTED_NO_CANDIDATE_FREEZE"
    assert result["candidate_frozen"] is False
    assert result["blind_data_used"] is False
    assert result["result"]["ATM_cells_passed_out_of_4"] == 0
    assert result["result"]["one_strike_ITM_cells_passed_out_of_4"] == 0


def test_session_anchor_math_uses_actual_volume_and_resets_by_session():
    frame = pd.DataFrame({
        "date": ["2026-01-01", "2026-01-01", "2026-01-02"],
        "futures_high": [102.0, 112.0, 202.0],
        "futures_low": [98.0, 108.0, 198.0],
        "futures_close": [101.0, 111.0, 201.0],
        "futures_volume": [10.0, 30.0, 20.0],
    })
    out = _add_anchor_features(frame)
    tp1 = (102.0 + 98.0 + 101.0) / 3.0
    tp2 = (112.0 + 108.0 + 111.0) / 3.0
    tp3 = (202.0 + 198.0 + 201.0) / 3.0
    assert np.isclose(out.loc[0, "session_anchor"], tp1)
    assert np.isclose(out.loc[1, "session_anchor"], (tp1 * 10 + tp2 * 30) / 40)
    assert np.isclose(out.loc[2, "session_anchor"], tp3)
    assert out.loc[1, "direction"] == -1.0


def test_session_anchor_grid_and_source_are_frozen():
    assert SOURCE["event_dataset_sha256"] == (
        "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f"
    )
    assert SEARCH_GRID["abs_anchor_deviation_percentile_min"] == [80, 90]
    assert SEARCH_GRID["options_fast_lead_filter"] == [
        "off", "reversion_direction_agreement"
    ]
    assert SEARCH_GRID["fixed_exit_minutes"] == [5, 10, 15, 30]
    assert SEARCH_GRID["total_formulations"] == 16


def test_session_anchor_gate_requires_cross_cohort_stability():
    summary = {
        "trades": 200,
        "trades_by_cohort": {"cohort1": 100, "cohort2": 100},
        "cohort1_mean_bps": 1.0,
        "cohort2_mean_bps": 0.5,
        "positive_chronological_blocks": 10,
        "session_cluster_bootstrap_mean_95pct_bps": [0.1, 1.5],
    }
    passed, failures = _passes(summary)
    assert passed is True
    assert failures == []
    summary["cohort2_mean_bps"] = -0.1
    passed, failures = _passes(summary)
    assert passed is False
    assert "cohort2_mean_not_positive" in failures


def test_session_anchor_guardrails_are_distinct_and_keep_blind_closed():
    assert GUARDRAILS["blind_data_used"] is False
    assert GUARDRAILS["fresh_blind_data_must_not_be_loaded"] is True
    assert GUARDRAILS["no_futures_spot_dislocation_filter"] is True
    assert GUARDRAILS["no_breakout_filter"] is True
    assert GUARDRAILS["no_wick_filter"] is True
    assert GUARDRAILS["no_OI_filter"] is True
    assert GUARDRAILS["no_time_filter"] is True
    assert GUARDRAILS["no_post_hoc_rescue"] is True
    assert GUARDRAILS["exact_option_pnl_forbidden_until_structural_pass_and_separate_protocol"] is True
