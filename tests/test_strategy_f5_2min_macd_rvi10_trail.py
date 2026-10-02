from datetime import datetime, timedelta

import pytest

from services.historical.strategy_f5_2min_macd_rvi10_trail_backtest import (
    _aggregate_2m,
    _eligible_entry,
    _trail_trade_simulation,
)
from services.historical.strategy_f5_2min_macd_rvi10_trail_protocol import (
    GUARDRAILS,
    MACD,
    PROTOCOL_VERSION,
    RELATIVE_VOLATILITY_INDEX,
    TRAIL,
    WINDOW,
)


def _one_min_rows():
    start = datetime.fromisoformat("2026-09-01T09:15:00+05:30")
    out = []
    for i, close in enumerate([100.0, 101.0, 102.0, 103.0]):
        ts = start + timedelta(minutes=i)
        out.append({
            "timestamp": ts.isoformat(),
            "date": "2026-09-01",
            "expiry": "2026-09-01",
            "strike": 25000,
            "right": "PE",
            "open": close - 0.5,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 10,
            "open_interest": 100,
        })
    return out


def test_f5_protocol_matches_user_setup():
    assert PROTOCOL_VERSION == "STRATEGY_F5_2MIN_MACD_RVI10_TRAIL10_V1"
    assert WINDOW["source_interval"] == "1minute"
    assert WINDOW["signal_interval_minutes"] == 2
    assert MACD["fast_length"] == 12
    assert MACD["slow_length"] == 26
    assert MACD["signal_length"] == 9
    assert RELATIVE_VOLATILITY_INDEX["stddev_length"] == 10
    assert RELATIVE_VOLATILITY_INDEX["entry_threshold"] == 50.0
    assert TRAIL["activation_return_pct"] == 10.0
    assert TRAIL["distance_pct_of_initial_trade_capital"] == 10.0
    assert TRAIL["intrabar_low_touch_exit"] is False
    assert TRAIL["after_activation_ignore_bearish_macd"] is True
    assert GUARDRAILS["live_execution"] is False
    assert GUARDRAILS["paper_execution"] is False


def test_one_minute_rows_aggregate_to_session_aligned_two_minute_bars():
    bars, incomplete = _aggregate_2m(_one_min_rows())

    assert incomplete == []
    assert len(bars) == 2
    assert bars[0]["timestamp"] == "2026-09-01T09:15:00+05:30"
    assert bars[0]["open"] == pytest.approx(99.5)
    assert bars[0]["high"] == pytest.approx(102.0)
    assert bars[0]["low"] == pytest.approx(99.0)
    assert bars[0]["close"] == pytest.approx(101.0)
    assert bars[1]["timestamp"] == "2026-09-01T09:17:00+05:30"
    assert bars[1]["close"] == pytest.approx(103.0)


def test_rvi50_is_required_at_bullish_macd_entry():
    assert _eligible_entry({
        "bullish_cross": True,
        "rvi": 50.0,
    })
    assert not _eligible_entry({
        "bullish_cross": True,
        "rvi": 49.99,
    })
    assert not _eligible_entry({
        "bullish_cross": False,
        "rvi": 80.0,
    })


def _obs(
    ts: str,
    close: float,
    *,
    bullish=False,
    bearish=False,
    rvi=60.0,
):
    return {
        "date": "2026-09-01",
        "month": "2026-09",
        "decision_timestamp": ts,
        "bar_start": ts,
        "expiry": "2026-09-01",
        "strike": 25000,
        "right": "PE",
        "close": close,
        "macd": 1.0,
        "macd_signal": 0.0,
        "rvi": rvi,
        "bullish_cross": bullish,
        "bearish_cross": bearish,
    }


def test_trail_activates_at_10pct_and_ignores_same_bar_bearish_cross():
    daily = [{
        "date": "2026-09-01",
        "month": "2026-09",
        "spot_0915_open": 25000.0,
        "strike": 25000,
        "expiry": "2026-09-01",
    }]
    obs = {
        "2026-09-01": {
            "2026-09-01T09:17:00+05:30": {
                "PE": _obs(
                    "2026-09-01T09:17:00+05:30",
                    100.0,
                    bullish=True,
                )
            },
            # +10% close and bearish cross: activation wins, no exit.
            "2026-09-01T09:19:00+05:30": {
                "PE": _obs(
                    "2026-09-01T09:19:00+05:30",
                    110.0,
                    bearish=True,
                )
            },
            # Peak +18 -> floor becomes +8 for next bar.
            "2026-09-01T09:21:00+05:30": {
                "PE": _obs("2026-09-01T09:21:00+05:30", 118.0)
            },
            # Peak +27 -> floor becomes +17 for next bar.
            "2026-09-01T09:23:00+05:30": {
                "PE": _obs("2026-09-01T09:23:00+05:30", 127.0)
            },
            # Close +16 breaches previously active +17 floor.
            "2026-09-01T09:25:00+05:30": {
                "PE": _obs("2026-09-01T09:25:00+05:30", 116.0)
            },
        }
    }
    open_2m = {
        ("2026-09-01T09:17:00+05:30", "2026-09-01", 25000, "PE"): 100.0,
        ("2026-09-01T09:25:00+05:30", "2026-09-01", 25000, "PE"): 116.0,
    }

    trades, skips = _trail_trade_simulation(daily, obs, open_2m, {})

    assert skips == []
    assert len(trades) == 1
    trade = trades[0]
    assert trade["trail_activated"] is True
    assert trade["trail_activation_timestamp"] == "2026-09-01T09:19:00+05:30"
    assert trade["peak_close_return_pct"] == pytest.approx(27.0)
    assert trade["trail_floor_return_pct"] == pytest.approx(17.0)
    assert trade["exit_timestamp"] == "2026-09-01T09:25:00+05:30"
    assert trade["exit_reason"] == "CLOSE_CONFIRMED_TRAIL10"


def test_pre_activation_bearish_macd_still_exits_failed_setup():
    daily = [{
        "date": "2026-09-01",
        "month": "2026-09",
        "spot_0915_open": 25000.0,
        "strike": 25000,
        "expiry": "2026-09-01",
    }]
    obs = {
        "2026-09-01": {
            "2026-09-01T09:17:00+05:30": {
                "PE": _obs(
                    "2026-09-01T09:17:00+05:30",
                    100.0,
                    bullish=True,
                )
            },
            "2026-09-01T09:19:00+05:30": {
                "PE": _obs(
                    "2026-09-01T09:19:00+05:30",
                    104.0,
                    bearish=True,
                )
            },
        }
    }
    open_2m = {
        ("2026-09-01T09:17:00+05:30", "2026-09-01", 25000, "PE"): 100.0,
        ("2026-09-01T09:19:00+05:30", "2026-09-01", 25000, "PE"): 104.0,
    }

    trades, skips = _trail_trade_simulation(daily, obs, open_2m, {})

    assert skips == []
    assert len(trades) == 1
    assert trades[0]["trail_activated"] is False
    assert trades[0]["exit_reason"] == "PRE_TRAIL_BEARISH_MACD_CROSS"
