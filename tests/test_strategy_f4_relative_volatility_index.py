from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from services.historical.strategy_f4_relative_volatility_index_exploration import (
    _candidate_name,
    _eligible,
    _indicator_frames,
    _stable_regions,
)
from services.historical.strategy_f4_relative_volatility_index_protocol import (
    GUARDRAILS,
    MACD,
    PROTOCOL_VERSION,
    RELATIVE_VOLATILITY_INDEX,
    ROBUSTNESS_SCREEN,
)


def _rows(n=50):
    start = datetime.fromisoformat("2026-07-01T09:15:00+05:30")
    closes = []
    value = 100.0
    for i in range(n):
        # Deterministic alternating directional volatility.
        value += 2.0 if i % 3 != 0 else -1.0
        closes.append(value)

    rows = []
    prev = closes[0]
    for i, close in enumerate(closes):
        open_ = prev if i else close - 0.5
        rows.append({
            "timestamp": (start + timedelta(minutes=5 * i)).isoformat(),
            "date": "2026-07-01",
            "expiry": "2026-07-07",
            "strike": 24000,
            "right": "CE",
            "open": open_,
            "high": max(open_, close) + 1.0,
            "low": min(open_, close) - 1.0,
            "close": close,
        })
        prev = close
    return rows


def test_corrected_f4_is_relative_volatility_index_0_to_100():
    assert PROTOCOL_VERSION == "STRATEGY_F4_MACD_RELATIVE_VOLATILITY_INDEX10_V1"
    assert RELATIVE_VOLATILITY_INDEX["name"] == "RELATIVE_VOLATILITY_INDEX"
    assert RELATIVE_VOLATILITY_INDEX["stddev_length"] == 10
    assert RELATIVE_VOLATILITY_INDEX["directional_ema_length"] == 14
    assert RELATIVE_VOLATILITY_INDEX["centerline"] == 50.0
    assert RELATIVE_VOLATILITY_INDEX["thresholds"] == [
        50, 55, 60, 65, 70, 75, 80
    ]
    assert MACD["fast_length"] == 12
    assert MACD["slow_length"] == 26
    assert MACD["signal_length"] == 9
    assert GUARDRAILS[
        "previous_relative_vigor_results_invalid_for_this_indicator"
    ] is True


def test_relative_volatility_index_matches_tradingview_style_formula():
    rows = _rows()
    frame = _indicator_frames(rows)[("2026-07-07", 24000, "CE")]

    close = pd.Series([r["close"] for r in rows], dtype=float)
    stddev = close.rolling(10, min_periods=10).std(ddof=0)
    change = close.diff()
    upper_input = stddev.where(change > 0.0, 0.0)
    lower_input = stddev.where(change <= 0.0, 0.0)
    upper = upper_input.ewm(span=14, adjust=False).mean()
    lower = lower_input.ewm(span=14, adjust=False).mean()
    denom = upper + lower
    expected = (100.0 * upper / denom).where(denom != 0.0, 50.0)

    actual = frame["relative_volatility_index"]
    assert actual.iloc[-1] == pytest.approx(expected.iloc[-1])
    finite = actual.dropna()
    assert (finite >= 0.0).all()
    assert (finite <= 100.0).all()


def test_threshold_entry_is_50_plus_as_requested():
    event = {
        "event": "BULLISH_CROSS",
        "relative_volatility_index": 54.9,
    }
    assert _candidate_name(50) == "RVI_GE_50"
    assert _eligible(event, 50)
    assert not _eligible(event, 55)
    assert _eligible(event, None)


def test_stable_region_requires_adjacent_passing_thresholds():
    reports = {}
    for threshold in RELATIVE_VOLATILITY_INDEX["thresholds"]:
        reports[f"RVI_GE_{threshold}"] = {
            "robustness_screen": {
                "passed": threshold in {55, 60, 70}
            }
        }

    regions = _stable_regions(reports)

    assert regions == [
        {
            "lower": 55,
            "upper": 60,
            "step": 5,
            "interpretation": (
                "adjacent thresholds both passed the frozen robustness screen"
            ),
        }
    ]


def test_robustness_requires_each_month_and_slippage():
    assert ROBUSTNESS_SCREEN["minimum_trades_pooled"] == 30
    assert ROBUSTNESS_SCREEN["minimum_trades_each_month"] == 8
    assert ROBUSTNESS_SCREEN["require_positive_net_each_month"] is True
    assert ROBUSTNESS_SCREEN[
        "require_profit_factor_above_one_each_month"
    ] is True
    assert ROBUSTNESS_SCREEN[
        "require_positive_net_at_0_5_slippage_each_month"
    ] is True
    assert ROBUSTNESS_SCREEN[
        "stable_threshold_region_requires_adjacent_threshold_pass"
    ] is True
