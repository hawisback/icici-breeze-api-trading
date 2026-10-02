import pandas as pd

from services.historical.independent_short_swing_compression_release_continuation_findings import (
    _add_features,
    _passes,
)
from services.historical.independent_short_swing_compression_release_continuation_protocol import (
    FROZEN_RULE,
    GUARDRAILS,
    SEARCH_GRID,
    SOURCE,
)


def _bar(ts, open_, high, low, close):
    return {
        "cohort": "cohort1",
        "date": str(pd.Timestamp(ts).date()),
        "timestamp": pd.Timestamp(ts),
        "futures_open": open_,
        "futures_high": high,
        "futures_low": low,
        "futures_close": close,
        "entry_gap_bps": 0.0,
    }


def test_compression_release_features_use_prior_bars_only():
    rows = [
        _bar("2026-01-02 09:15", 100.0, 100.10, 100.00, 100.05),
        _bar("2026-01-02 09:20", 100.0, 100.10, 100.00, 100.05),
        _bar("2026-01-02 09:25", 100.0, 100.10, 100.00, 100.05),
        _bar("2026-01-02 09:30", 100.0, 100.05, 100.00, 100.03),
        _bar("2026-01-02 09:35", 100.0, 100.05, 100.00, 100.03),
        _bar("2026-01-02 09:40", 100.0, 100.05, 100.00, 100.03),
        _bar("2026-01-02 09:45", 100.0, 100.11, 100.00, 100.10),
    ]
    out = _add_features(pd.DataFrame(rows))
    signal = out.iloc[-1]
    assert signal["compression_ratio"] < 0.75
    assert signal["release_expansion_ratio"] > 1.5
    assert signal["current_close_location"] >= 0.75
    assert signal["bar_body_bps"] > 0
    assert signal["direction"] == 1.0


def test_compression_lookbacks_do_not_cross_session_boundary():
    rows = [
        _bar("2026-01-02 15:15", 100.0, 100.10, 100.00, 100.05),
        _bar("2026-01-02 15:20", 100.0, 100.10, 100.00, 100.05),
        _bar("2026-01-02 15:25", 100.0, 100.10, 100.00, 100.05),
        _bar("2026-01-05 09:15", 100.0, 100.20, 100.00, 100.18),
    ]
    out = _add_features(pd.DataFrame(rows))
    first_new_session = out[out["date"] == "2026-01-05"].iloc[0]
    assert pd.isna(first_new_session["prior3_mean_range_bps"])
    assert pd.isna(first_new_session["compression_ratio"])


def test_compression_release_rule_and_grid_are_frozen():
    assert SOURCE["event_dataset_sha256"] == (
        "4c034b50cb9b1f3552e890076e42cdcceda6ad925ff6d2afc8659443739f6a6f"
    )
    assert FROZEN_RULE["compression_ratio_max"] == 0.75
    assert FROZEN_RULE["release_expansion_ratio_min"] == 1.50
    assert FROZEN_RULE["long_close_location_min"] == 0.75
    assert FROZEN_RULE["short_close_location_max"] == 0.25
    assert SEARCH_GRID["options_fast_lead_filter"] == ["off"]
    assert SEARCH_GRID["fixed_exit_minutes"] == [5, 10, 15, 30]
    assert SEARCH_GRID["total_formulations"] == 4


def test_structural_gate_still_requires_cross_cohort_stability():
    summary = {
        "trades": 120,
        "trades_by_cohort": {"cohort1": 60, "cohort2": 60},
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


def test_guardrails_keep_blind_closed_and_forbid_rescue():
    assert GUARDRAILS["blind_data_used"] is False
    assert GUARDRAILS["fresh_blind_data_must_not_be_loaded"] is True
    assert GUARDRAILS["no_price_level_breakout_filter"] is True
    assert GUARDRAILS["no_inside_pause_filter"] is True
    assert GUARDRAILS["no_options_fast_lead_filter"] is True
    assert GUARDRAILS["no_time_filter"] is True
    assert GUARDRAILS["no_DTE_filter"] is True
    assert GUARDRAILS["no_stop_target_search"] is True
    assert GUARDRAILS["no_post_hoc_rescue"] is True
    assert GUARDRAILS["strategy_d_remains_paused"] is True
