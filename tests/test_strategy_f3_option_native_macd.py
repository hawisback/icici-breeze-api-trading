from datetime import date, datetime, timedelta

import pandas as pd
import pytest

import services.historical.strategy_f3_option_native_macd_backtest as backtest
from services.historical.strategy_f3_option_native_macd_market import (
    _atm_strike,
    _contract_request_plan,
    _daily_contracts,
    _nearest_expiry,
)
from services.historical.strategy_f3_option_native_macd_protocol import (
    EXECUTION,
    GUARDRAILS,
    MACD,
    OPTION_SELECTION,
    PROTOCOL_VERSION,
    SIGNALS,
)


def _spot_day(day: str, opening: float) -> list[dict]:
    start = datetime.fromisoformat(f"{day}T09:15:00+05:30")
    rows = []
    px = opening
    for i in range(75):
        rows.append(
            {
                "timestamp": (start + timedelta(minutes=5 * i)).isoformat(),
                "date": day,
                "open": px,
                "high": px + 5,
                "low": px - 5,
                "close": px + 1,
            }
        )
        px += 1
    return rows


def test_f3_protocol_is_option_native_not_spot_macd():
    assert PROTOCOL_VERSION == "STRATEGY_F3_OPTION_NATIVE_MACD_V1"
    assert MACD["source"] == "option_close"
    assert MACD["fast_length"] == 12
    assert MACD["slow_length"] == 26
    assert MACD["signal_length"] == 9
    assert MACD["minimum_prior_option_bars_before_session"] == 35
    assert OPTION_SELECTION["fixed_strike_for_entire_session"] is True
    assert OPTION_SELECTION["daily_atm_reference"] == "09:15_NIFTY_SPOT_OPEN"
    assert "OPTION_MACD" in SIGNALS["entry"]
    assert GUARDRAILS["no_dynamic_atm_splicing"] is True
    assert GUARDRAILS["live_execution"] is False
    assert GUARDRAILS["paper_execution"] is False


@pytest.mark.parametrize(
    ("price", "expected"),
    [(25024.9, 25000), (25025.0, 25050), (25075.0, 25100)],
)
def test_f3_atm_rounding(price, expected):
    assert _atm_strike(price) == expected


def test_f3_expiry_selection_includes_zero_dte():
    assert _nearest_expiry(date(2026, 9, 1)) == date(2026, 9, 1)
    assert _nearest_expiry(date(2026, 9, 2)) == date(2026, 9, 8)
    assert _nearest_expiry(date(2026, 9, 30)) == date(2026, 10, 6)


def test_daily_contract_is_fixed_from_0915_spot_open():
    rows = _spot_day("2026-09-03", 25024.0)
    # Later spot movement must not change the selected option strike.
    for row in rows[10:]:
        row["open"] = 25380.0
        row["close"] = 25390.0

    selected = _daily_contracts(rows)

    assert len(selected) == 1
    assert selected[0]["strike"] == 25000
    assert selected[0]["spot_0915_open"] == 25024.0


def test_contract_plan_requests_prior_sessions_for_exact_contract():
    rows = []
    for day in (
        "2026-08-27",
        "2026-08-28",
        "2026-08-31",
        "2026-09-01",
        "2026-09-02",
        "2026-09-03",
    ):
        rows.extend(_spot_day(day, 25000.0))
    selected = [
        {
            "date": "2026-09-03",
            "spot_0915_open": 25000.0,
            "strike": 25000,
            "expiry": "2026-09-08",
        }
    ]

    plan = _contract_request_plan(rows, selected)

    assert set(plan) == {
        ("2026-09-08", 25000, "CE"),
        ("2026-09-08", 25000, "PE"),
    }
    assert len(plan[("2026-09-08", 25000, "CE")]) == 6
    assert plan[("2026-09-08", 25000, "CE")][-1] == date(2026, 9, 3)


def test_option_macd_is_computed_per_exact_contract():
    start = datetime.fromisoformat("2026-08-31T09:15:00+05:30")
    rows = []
    for right, base, sign in (("CE", 100.0, 1.0), ("PE", 200.0, -1.0)):
        for i in range(50):
            ts = start + timedelta(minutes=5 * i)
            rows.append(
                {
                    "timestamp": ts.isoformat(),
                    "date": ts.date().isoformat(),
                    "expiry": "2026-09-01",
                    "strike": 25000,
                    "right": right,
                    "open": base + sign * i,
                    "high": base + sign * i + 1,
                    "low": max(0.01, base + sign * i - 1),
                    "close": base + sign * i,
                }
            )

    frames = backtest._macd_by_contract(rows)

    assert set(frames) == {
        ("2026-09-01", 25000, "CE"),
        ("2026-09-01", 25000, "PE"),
    }
    assert frames[("2026-09-01", 25000, "CE")].iloc[-1]["macd"] > 0
    assert frames[("2026-09-01", 25000, "PE")].iloc[-1]["macd"] < 0


def _event(ts, right, event):
    return {
        "date": ts[:10],
        "event_timestamp": ts,
        "bar_start": ts,
        "expiry": "2026-09-08",
        "strike": 25000,
        "right": right,
        "event": event,
        "option_close": 100.0,
        "macd": 1.0 if event == "BULLISH_CROSS" else -1.0,
        "macd_signal": 0.0,
    }


def test_same_timestamp_exit_then_opposite_option_entry_is_allowed():
    daily = [
        {
            "date": "2026-09-03",
            "spot_0915_open": 25000.0,
            "strike": 25000,
            "expiry": "2026-09-08",
        }
    ]
    events = [
        _event("2026-09-03T10:00:00+05:30", "CE", "BULLISH_CROSS"),
        _event("2026-09-03T11:00:00+05:30", "CE", "BEARISH_CROSS"),
        _event("2026-09-03T11:00:00+05:30", "PE", "BULLISH_CROSS"),
        _event("2026-09-03T12:00:00+05:30", "PE", "BEARISH_CROSS"),
    ]

    intents, ambiguous = backtest._build_trade_intents(daily, events)

    assert ambiguous == []
    assert len(intents) == 2
    assert intents[0]["right"] == "CE"
    assert intents[0]["entry_timestamp"] == "2026-09-03T10:00:00+05:30"
    assert intents[0]["exit_timestamp"] == "2026-09-03T11:00:00+05:30"
    assert intents[1]["right"] == "PE"
    assert intents[1]["entry_timestamp"] == "2026-09-03T11:00:00+05:30"
    assert intents[1]["exit_timestamp"] == "2026-09-03T12:00:00+05:30"


def test_simultaneous_ce_pe_bullish_cross_is_skipped_as_ambiguous():
    daily = [
        {
            "date": "2026-09-03",
            "spot_0915_open": 25000.0,
            "strike": 25000,
            "expiry": "2026-09-08",
        }
    ]
    events = [
        _event("2026-09-03T10:00:00+05:30", "CE", "BULLISH_CROSS"),
        _event("2026-09-03T10:00:00+05:30", "PE", "BULLISH_CROSS"),
    ]

    intents, ambiguous = backtest._build_trade_intents(daily, events)

    assert intents == []
    assert len(ambiguous) == 1
    assert ambiguous[0]["reason"] == "SIMULTANEOUS_CE_PE_BULLISH_CROSS"


def test_no_entry_at_force_exit():
    daily = [
        {
            "date": "2026-09-03",
            "spot_0915_open": 25000.0,
            "strike": 25000,
            "expiry": "2026-09-08",
        }
    ]
    events = [_event("2026-09-03T15:20:00+05:30", "CE", "BULLISH_CROSS")]
    intents, _ = backtest._build_trade_intents(daily, events)
    assert intents == []
    assert EXECUTION["force_exit_price"] == "15:20_OPTION_BAR_OPEN"
