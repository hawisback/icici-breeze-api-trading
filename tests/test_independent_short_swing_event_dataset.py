import pandas as pd
import pytest

from services.historical.independent_short_swing_development_protocol import (
    CORPUS_ROLE,
    DEVELOPMENT_RULES,
    FIXED_EXIT_HORIZONS_MINUTES,
    PROTOCOL_VERSION,
    STRATEGY_FAMILIES,
)
from services.historical.independent_short_swing_event_dataset import (
    _add_development_features,
    _attach_execution_outcomes,
)


def test_short_swing_development_protocol_guardrails():
    assert PROTOCOL_VERSION == "SHORT_SWING_DEVELOPMENT_V1"
    assert CORPUS_ROLE == "INSPECTED_STRATEGY_DEVELOPMENT_NOT_VALIDATION"
    assert FIXED_EXIT_HORIZONS_MINUTES == (5, 10, 15, 30)
    assert STRATEGY_FAMILIES == (
        "movement_conditioned_short_mean_reversion",
        "movement_conditioned_breakout_continuation",
        "failed_breakout_reversal",
    )
    assert DEVELOPMENT_RULES["blind_data_used"] is False
    assert DEVELOPMENT_RULES["implementation_allowed"] is False
    assert DEVELOPMENT_RULES["b2_target_sizing_restart"] is False
    assert DEVELOPMENT_RULES["strategy_d_remains_paused"] is True


def test_execution_outcomes_use_t_plus_five_open_and_fixed_minute_closes():
    timestamp = pd.Timestamp("2026-01-02T09:15:00+05:30")
    frame = pd.DataFrame(
        [{
            "timestamp": timestamp,
            "futures_close": 100.0,
        }]
    )
    rows = []
    for offset in range(5, 35):
        rows.append(
            {
                "timestamp": (timestamp + pd.Timedelta(minutes=offset)).isoformat(),
                "open": 100.0 + offset,
                "high": 101.0 + offset,
                "low": 99.0 + offset,
                "close": 100.5 + offset,
            }
        )
    result = _attach_execution_outcomes(frame, {"rows": rows})
    row = result.iloc[0]

    assert row["entry_price"] == 105.0
    assert row["entry_gap_bps"] == pytest.approx(500.0)
    assert row["h5m_exit_price"] == 109.5
    assert row["h10m_exit_price"] == 114.5
    assert row["h15m_exit_price"] == 119.5
    assert row["h30m_exit_price"] == 134.5
    assert row["h5m_long_mfe_bps"] == pytest.approx((110.0 / 105.0 - 1.0) * 10000.0)
    assert row["h5m_long_mae_bps"] == pytest.approx((104.0 / 105.0 - 1.0) * 10000.0)
    assert row["h5m_short_mfe_bps"] == pytest.approx(-row["h5m_long_mae_bps"])
    assert row["h5m_short_mae_bps"] == pytest.approx(-row["h5m_long_mfe_bps"])
    assert row["h5m_short_terminal_bps"] == pytest.approx(-row["h5m_terminal_bps"])


def test_execution_outcomes_do_not_use_partial_horizon_near_session_end():
    timestamp = pd.Timestamp("2026-01-02T15:20:00+05:30")
    frame = pd.DataFrame(
        [{
            "timestamp": timestamp,
            "futures_close": 100.0,
        }]
    )
    rows = []
    for offset in range(5, 10):
        rows.append(
            {
                "timestamp": (timestamp + pd.Timedelta(minutes=offset)).isoformat(),
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
            }
        )
    result = _attach_execution_outcomes(frame, {"rows": rows})
    row = result.iloc[0]
    assert pd.notna(row["h5m_terminal_bps"])
    assert pd.isna(row["h10m_terminal_bps"])
    assert pd.isna(row["h15m_terminal_bps"])
    assert pd.isna(row["h30m_terminal_bps"])


def test_failed_breakout_features_are_directionally_defined():
    rows = []
    bars = [
        (100.0, 102.0, 99.0, 101.0, 1000.0),
        (101.0, 103.0, 100.0, 102.0, 1100.0),
        (102.0, 104.0, 101.0, 103.0, 1200.0),
        (103.0, 105.0, 102.0, 103.5, 1300.0),
    ]
    for block, day in ((1, "2026-01-02"), (2, "2026-01-05")):
        base = pd.Timestamp(f"{day}T09:15:00+05:30")
        for index, (open_, high, low, close, volume) in enumerate(bars):
            rows.append(
                {
                    "timestamp": base + pd.Timedelta(minutes=5 * index),
                    "date": day,
                    "block": block,
                    "futures_open": open_,
                    "futures_high": high,
                    "futures_low": low,
                    "futures_close": close,
                    "futures_volume": volume,
                    "options_gap_bps": float(index + block),
                    "spot_gap_bps": float(block) / 10.0,
                    "futures_return_bps": float(index),
                }
            )
    frame = pd.DataFrame(rows)
    result = _add_development_features(frame)
    row = result.iloc[-1]
    assert bool(row["failed_breakout_up"]) is True
    assert bool(row["closed_breakout_up"]) is False
    assert row["up_breakout_bps"] > 0
