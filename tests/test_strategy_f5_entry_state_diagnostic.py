from datetime import datetime, timedelta

import pytest

from services.historical.strategy_f5_entry_state_diagnostic import (
    _feature_lookup,
    _natural_state_report,
    _rank_auc,
)
from services.historical.strategy_f5_entry_state_diagnostic_protocol import (
    CONTINUOUS_FEATURES,
    GUARDRAILS,
    NATURAL_STATES,
    PRIMARY_LABEL,
    PROTOCOL_VERSION,
)


def _bars(n=50):
    start = datetime.fromisoformat("2026-09-01T09:15:00+05:30")
    bars = []
    close = 100.0
    for i in range(n):
        close += 1.5 if i % 4 != 0 else -0.5
        bars.append({
            "timestamp": (start + timedelta(minutes=2 * i)).isoformat(),
            "date": "2026-09-01",
            "expiry": "2026-09-01",
            "strike": 25000,
            "right": "PE",
            "open": close - 0.4,
            "high": close + 0.8,
            "low": close - 0.8,
            "close": close,
            "volume": 100 + i * 3,
            "open_interest": 1000,
        })
    return bars


def test_entry_state_protocol_is_diagnostic_only():
    assert PROTOCOL_VERSION == "STRATEGY_F5_ENTRY_STATE_DIAGNOSTIC_V1"
    assert PRIMARY_LABEL == "TRAIL_ACTIVATED"
    assert "rvi_delta_1" in CONTINUOUS_FEATURES
    assert "macd_hist_acceleration_pct" in CONTINUOUS_FEATURES
    assert "return_3bar_pct" in CONTINUOUS_FEATURES
    assert "volume_ratio_5" in CONTINUOUS_FEATURES
    assert "RVI_RISING_AND_HIST_ACCELERATING" in NATURAL_STATES
    assert GUARDRAILS["no_numeric_cutpoint_search"] is True
    assert GUARDRAILS["keep_existing_post_activation_trail_frozen"] is True
    assert GUARDRAILS["live_execution"] is False


def test_feature_lookup_uses_completed_signal_bar_for_next_open_decision():
    bars = _bars()
    lookup = _feature_lookup(bars)
    key = (
        "2026-09-01T10:35:00+05:30",
        "2026-09-01",
        25000,
        "PE",
    )
    assert key in lookup
    row = lookup[key]
    assert row["rvi_level"] is not None
    assert row["rvi_delta_1"] is not None
    assert row["macd_hist_acceleration_pct"] is not None
    assert row["return_3bar_pct"] is not None
    assert row["volume_ratio_5"] is not None


def test_rank_auc_is_one_when_all_activated_values_are_higher():
    import numpy as np

    pos = np.asarray([3.0, 4.0, 5.0])
    neg = np.asarray([0.0, 1.0, 2.0])
    assert _rank_auc(pos, neg) == pytest.approx(1.0)


def test_natural_state_report_compares_selected_to_all_without_filtering_outcomes():
    rows = [
        {
            "RVI_RISING": True,
            "trail_activated": True,
            "baseline_winner": True,
            "baseline_net_pnl_inr": 500.0,
        },
        {
            "RVI_RISING": True,
            "trail_activated": False,
            "baseline_winner": False,
            "baseline_net_pnl_inr": -100.0,
        },
        {
            "RVI_RISING": False,
            "trail_activated": False,
            "baseline_winner": False,
            "baseline_net_pnl_inr": -200.0,
        },
        {
            "RVI_RISING": False,
            "trail_activated": False,
            "baseline_winner": False,
            "baseline_net_pnl_inr": -100.0,
        },
    ]
    report = _natural_state_report(rows, "RVI_RISING")
    assert report["selected"]["trades"] == 2
    assert report["selected"]["trail_activation_rate_pct"] == 50.0
    assert report["all_available"]["trail_activation_rate_pct"] == 25.0
    assert report["activation_rate_lift_vs_all"] == pytest.approx(2.0)
