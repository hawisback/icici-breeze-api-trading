from datetime import datetime, timedelta

import pandas as pd
import pytest

import services.historical.strategy_f2_macd_histogram_market as market
from services.historical.strategy_f2_macd_histogram_backtest import (
    _trade_intents_for_variant,
)
from services.historical.strategy_f2_macd_histogram_protocol import (
    GUARDRAILS,
    PROTOCOL_VERSION,
    VARIANTS,
)


def _frame(hist_values):
    start = datetime.fromisoformat("2026-08-03T09:15:00+05:30")
    rows = []
    for i, hist in enumerate(hist_values):
        # signal=0, macd=hist makes crossover direction easy to control.
        rows.append(
            {
                "timestamp": start + timedelta(minutes=5 * i),
                "date": "2026-08-03",
                "open": 25000.0,
                "high": 25010.0,
                "low": 24990.0,
                "close": 25000.0 + i,
                "macd": float(hist),
                "signal": 0.0,
                "hist": float(hist),
                "macd_prev": float(hist_values[i - 1]) if i else float("nan"),
                "signal_prev": 0.0 if i else float("nan"),
            }
        )
    return pd.DataFrame(rows)


def test_protocol_freezes_two_confirmation_depths_only():
    assert PROTOCOL_VERSION == "STRATEGY_F2_MACD_HISTOGRAM_PERSISTENCE_V1"
    assert list(VARIANTS) == ["F2_1BAR", "F2_2BAR"]
    assert VARIANTS["F2_1BAR"]["confirmation_bars"] == 1
    assert VARIANTS["F2_2BAR"]["confirmation_bars"] == 2
    assert GUARDRAILS["september_2026_not_used_for_f2_scoring"] is True
    assert GUARDRAILS["no_extra_filters"] is True
    assert GUARDRAILS["no_stop_target_search"] is True


def test_one_bar_confirmation_requires_expansion_and_enters_after_confirmation(
    monkeypatch: pytest.MonkeyPatch,
):
    frame = _frame([-0.5, 0.2, 0.4, 0.3])
    monkeypatch.setattr(market, "_macd_frame", lambda rows: frame)

    entries = market._qualified_entries([{"dummy": 1}], 1)

    assert len(entries) == 1
    assert entries[0]["direction"] == "BULLISH"
    assert entries[0]["cross_bar_start"] == "2026-08-03T09:20:00+05:30"
    assert entries[0]["final_confirmation_bar_start"] == (
        "2026-08-03T09:25:00+05:30"
    )
    assert entries[0]["entry_timestamp"] == "2026-08-03T09:30:00+05:30"
    assert entries[0]["cross_histogram"] == pytest.approx(0.2)
    assert entries[0]["final_histogram"] == pytest.approx(0.4)


def test_one_bar_confirmation_rejects_nonexpanding_histogram(
    monkeypatch: pytest.MonkeyPatch,
):
    frame = _frame([-0.5, 0.4, 0.3, 0.2])
    monkeypatch.setattr(market, "_macd_frame", lambda rows: frame)
    assert market._qualified_entries([{"dummy": 1}], 1) == []


def test_two_bar_confirmation_requires_strict_expansion_each_bar(
    monkeypatch: pytest.MonkeyPatch,
):
    frame = _frame([0.5, -0.2, -0.4, -0.7, -0.6])
    monkeypatch.setattr(market, "_macd_frame", lambda rows: frame)

    entries = market._qualified_entries([{"dummy": 1}], 2)

    assert len(entries) == 1
    assert entries[0]["direction"] == "BEARISH"
    assert entries[0]["entry_timestamp"] == "2026-08-03T09:35:00+05:30"
    assert entries[0]["cross_histogram"] == pytest.approx(-0.2)
    assert entries[0]["final_histogram"] == pytest.approx(-0.7)


def test_two_bar_confirmation_rejects_second_bar_contraction(
    monkeypatch: pytest.MonkeyPatch,
):
    frame = _frame([0.5, -0.2, -0.5, -0.4])
    monkeypatch.setattr(market, "_macd_frame", lambda rows: frame)
    assert market._qualified_entries([{"dummy": 1}], 2) == []


def test_opposite_raw_cross_exits_without_confirmation_delay():
    qualified = [
        {
            "date": "2026-08-03",
            "entry_timestamp": "2026-08-03T10:00:00+05:30",
            "direction": "BULLISH",
            "right": "CE",
            "expiry": "2026-08-04",
            "strike": 25000,
            "signal_close": 25010.0,
            "cross_bar_start": "2026-08-03T09:50:00+05:30",
            "confirmation_bars": 1,
            "final_confirmation_bar_start": "2026-08-03T09:55:00+05:30",
            "cross_histogram": 0.2,
            "final_histogram": 0.4,
        }
    ]
    raw = [
        {
            "date": "2026-08-03",
            "direction": "BEARISH",
            "event_timestamp": "2026-08-03T10:25:00+05:30",
        }
    ]

    intents = _trade_intents_for_variant(qualified, raw)

    assert len(intents) == 1
    assert intents[0]["exit_timestamp"] == "2026-08-03T10:25:00+05:30"
    assert intents[0]["exit_reason"] == "OPPOSITE_RAW_MACD_CROSSOVER"


def test_no_raw_opposite_cross_forces_1520_exit():
    qualified = [
        {
            "date": "2026-08-03",
            "entry_timestamp": "2026-08-03T14:00:00+05:30",
            "direction": "BEARISH",
            "right": "PE",
            "expiry": "2026-08-04",
            "strike": 25000,
            "signal_close": 24990.0,
            "cross_bar_start": "2026-08-03T13:50:00+05:30",
            "confirmation_bars": 1,
            "final_confirmation_bar_start": "2026-08-03T13:55:00+05:30",
            "cross_histogram": -0.2,
            "final_histogram": -0.4,
        }
    ]
    intents = _trade_intents_for_variant(qualified, [])
    assert intents[0]["exit_timestamp"] == "2026-08-03T15:20:00+05:30"
    assert intents[0]["exit_reason"] == "FORCE_EXIT_15_20"
