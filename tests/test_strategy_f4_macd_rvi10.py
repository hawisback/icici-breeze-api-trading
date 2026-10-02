from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from services.historical.strategy_f4_macd_rvi10_exploration import (
    _eligible,
    _indicator_frames,
    _learn_cutpoints,
)
from services.historical.strategy_f4_macd_rvi10_protocol import (
    CANDIDATES,
    GUARDRAILS,
    MACD,
    PROTOCOL_VERSION,
    ROBUSTNESS_SCREEN,
    RVI,
    WINDOW,
)


def _option_rows(n=40):
    start = datetime.fromisoformat("2026-07-01T09:15:00+05:30")
    rows = []
    for i in range(n):
        o = 100.0 + i * 0.5
        c = o + (1.0 if i % 3 else -0.25)
        h = max(o, c) + 2.0
        l = min(o, c) - 2.0
        rows.append({
            "timestamp": (start + timedelta(minutes=5 * i)).isoformat(),
            "date": "2026-07-01",
            "expiry": "2026-07-07",
            "strike": 24000,
            "right": "CE",
            "open": o,
            "high": h,
            "low": l,
            "close": c,
        })
    return rows


def test_f4_protocol_is_fixed_rvi10_feature_study():
    assert PROTOCOL_VERSION == "STRATEGY_F4_OPTION_NATIVE_MACD_RVI10_EXPLORATION_V1"
    assert WINDOW["months"] == ["2026-07", "2026-08", "2026-09"]
    assert MACD["fast_length"] == 12
    assert MACD["slow_length"] == 26
    assert MACD["signal_length"] == 9
    assert RVI["indicator"] == "RELATIVE_VIGOR_INDEX"
    assert RVI["length"] == 10
    assert RVI["weighted_4bar_kernel"] == [1, 2, 2, 1]
    assert RVI["signal_weighted_4bar_kernel"] == [1, 2, 2, 1]
    assert len(CANDIDATES) == 8
    assert GUARDRAILS["no_rvi_length_search"] is True
    assert GUARDRAILS["no_thresholds_beyond_frozen_candidates"] is True


def test_tradingview_style_rvi_formula_matches_manual_last_value():
    rows = _option_rows(40)
    frames = _indicator_frames(rows)
    frame = frames[("2026-07-07", 24000, "CE")]

    open_ = pd.Series([r["open"] for r in rows], dtype=float)
    high = pd.Series([r["high"] for r in rows], dtype=float)
    low = pd.Series([r["low"] for r in rows], dtype=float)
    close = pd.Series([r["close"] for r in rows], dtype=float)

    co = close - open_
    hl = high - low
    wco = (co + 2 * co.shift(1) + 2 * co.shift(2) + co.shift(3)) / 6.0
    whl = (hl + 2 * hl.shift(1) + 2 * hl.shift(2) + hl.shift(3)) / 6.0
    expected_rvi = wco.rolling(10, min_periods=10).mean() / whl.rolling(
        10, min_periods=10
    ).mean()
    expected_signal = (
        expected_rvi
        + 2 * expected_rvi.shift(1)
        + 2 * expected_rvi.shift(2)
        + expected_rvi.shift(3)
    ) / 6.0

    assert frame.iloc[-1]["rvi"] == pytest.approx(expected_rvi.iloc[-1])
    assert frame.iloc[-1]["rvi_signal"] == pytest.approx(
        expected_signal.iloc[-1]
    )
    assert frame.iloc[-1]["rvi_spread"] == pytest.approx(
        expected_rvi.iloc[-1] - expected_signal.iloc[-1]
    )


def test_cutpoints_are_learned_once_from_positive_development_values():
    events = [
        {"event": "BULLISH_CROSS", "rvi": 0.10, "rvi_spread": 0.01},
        {"event": "BULLISH_CROSS", "rvi": 0.20, "rvi_spread": 0.02},
        {"event": "BULLISH_CROSS", "rvi": 0.30, "rvi_spread": 0.03},
        {"event": "BULLISH_CROSS", "rvi": 0.40, "rvi_spread": 0.04},
        {"event": "BULLISH_CROSS", "rvi": -0.50, "rvi_spread": -0.05},
        {"event": "BEARISH_CROSS", "rvi": 0.99, "rvi_spread": 0.99},
    ]
    cut = _learn_cutpoints(events)
    assert cut["rvi_level_positive_p50"] == pytest.approx(0.25)
    assert cut["rvi_level_positive_p75"] == pytest.approx(0.325)
    assert cut["rvi_spread_positive_p50"] == pytest.approx(0.025)
    assert cut["rvi_spread_positive_p75"] == pytest.approx(0.0325)
    assert cut["positive_level_observations"] == 4
    assert cut["positive_spread_observations"] == 4


def test_candidate_rules_use_frozen_cutpoints():
    cut = {
        "rvi_spread_positive_p50": 0.03,
        "rvi_spread_positive_p75": 0.05,
        "rvi_level_positive_p50": 0.20,
        "rvi_level_positive_p75": 0.30,
    }
    event = {
        "event": "BULLISH_CROSS",
        "rvi": 0.25,
        "rvi_signal": 0.20,
        "rvi_spread": 0.05,
    }
    assert _eligible(event, "BASELINE", cut)
    assert _eligible(event, "RVI_ABOVE_SIGNAL", cut)
    assert _eligible(event, "RVI_ABOVE_ZERO", cut)
    assert _eligible(event, "RVI_ABOVE_SIGNAL_AND_ZERO", cut)
    assert _eligible(event, "RVI_SPREAD_P50", cut)
    assert _eligible(event, "RVI_SPREAD_P75", cut)
    assert _eligible(event, "RVI_LEVEL_P50", cut)
    assert not _eligible(event, "RVI_LEVEL_P75", cut)


def test_robustness_screen_is_deliberately_monthwise_and_slippage_aware():
    assert ROBUSTNESS_SCREEN["minimum_trades_pooled"] == 30
    assert ROBUSTNESS_SCREEN["minimum_trades_each_month"] == 8
    assert ROBUSTNESS_SCREEN["require_positive_net_each_month"] is True
    assert ROBUSTNESS_SCREEN["require_profit_factor_above_one_each_month"] is True
    assert ROBUSTNESS_SCREEN[
        "require_positive_net_at_0_5_slippage_each_month"
    ] is True
    assert ROBUSTNESS_SCREEN["require_positive_pooled_net_at_1_0_slippage"] is True



def test_candidate_report_includes_monthly_side_breakdown():
    from services.historical.strategy_f4_macd_rvi10_exploration import (
        _candidate_report,
    )

    trades = [
        {
            "month": "2026-07",
            "right": "CE",
            "slippage_sensitivity": {
                "0.00": {"net_pnl_inr": -100.0},
                "0.50": {"net_pnl_inr": -150.0},
                "1.00": {"net_pnl_inr": -200.0},
            },
        },
        {
            "month": "2026-07",
            "right": "PE",
            "slippage_sensitivity": {
                "0.00": {"net_pnl_inr": 300.0},
                "0.50": {"net_pnl_inr": 250.0},
                "1.00": {"net_pnl_inr": 200.0},
            },
        },
    ]

    report = _candidate_report("BASELINE", trades, [], [])

    assert report["by_month_side"]["2026-07"]["CE"]["0.00"]["net_pnl_inr"] == -100.0
    assert report["by_month_side"]["2026-07"]["PE"]["0.00"]["net_pnl_inr"] == 300.0
