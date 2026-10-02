from datetime import datetime
from zoneinfo import ZoneInfo

from services.historical.strategy_f5_forward_regime_holdout import (
    _entry_time_regime,
    _gate,
    analyze,
)
from services.historical.strategy_f5_forward_regime_holdout_protocol import (
    EXCLUDED_PREVIOUSLY_INSPECTED_DATES,
    EXPIRIES,
    FROZEN_LATE_ENTRY_DIAGNOSTIC_CUTOFF,
    GUARDRAILS,
    PROTOCOL_VERSION,
    VALIDATION_GATE,
    WINDOW,
)

IST = ZoneInfo("Asia/Kolkata")


def _spot(ts: str, close: float, open_: float = 100.0):
    dt = datetime.fromisoformat(ts).replace(tzinfo=IST)
    return {
        "timestamp": dt.isoformat(),
        "date": dt.date().isoformat(),
        "open": open_,
        "high": max(open_, close),
        "low": min(open_, close),
        "close": close,
    }


def test_holdout_is_fresh_forward_and_excludes_october_1():
    assert PROTOCOL_VERSION == "STRATEGY_F5_FORWARD_REGIME_HOLDOUT_V1"
    assert WINDOW["start"] == "2026-10-05"
    assert WINDOW["end"] == "2026-12-29"
    assert "2026-10-01" in EXCLUDED_PREVIOUSLY_INSPECTED_DATES
    assert GUARDRAILS["fresh_forward_holdout"] is True
    assert GUARDRAILS["no_threshold_search_on_holdout"] is True


def test_holiday_shifted_tuesday_expiries_are_frozen():
    assert "2026-10-19" in EXPIRIES
    assert "2026-11-09" in EXPIRIES
    assert "2026-11-23" in EXPIRIES
    assert EXPIRIES[-1] == "2026-12-29"


def test_entry_time_regime_uses_only_completed_5m_bars():
    rows = [
        _spot("2026-10-05T09:15:00", 99.0),
        _spot("2026-10-05T09:20:00", 98.0, 99.0),
        _spot("2026-10-05T09:25:00", 120.0, 98.0),
    ]
    context = _entry_time_regime(rows, "2026-10-05T09:25:00+05:30")
    assert context["available"] is True
    assert context["completed_5m_bars"] == 2
    assert context["last_completed_bar_start"].startswith("2026-10-05T09:20")
    assert context["regime"] == "BEARISH"


def test_entry_time_regime_is_unavailable_before_two_completed_bars():
    rows = [
        _spot("2026-10-05T09:15:00", 99.0),
        _spot("2026-10-05T09:20:00", 98.0, 99.0),
    ]
    context = _entry_time_regime(rows, "2026-10-05T09:22:00+05:30")
    assert context["available"] is False
    assert context["reason"] == "INSUFFICIENT_COMPLETED_5M_BARS"


def _trade(pnl: float, activated: bool):
    return {"net_pnl_inr": pnl, "trail_activated": activated}


def test_gate_passes_only_when_frozen_bearish_relationship_holds():
    pe = [_trade(100.0, True) for _ in range(20)]
    ce = [_trade(-50.0, False) for _ in range(10)]
    daily = {
        f"2026-10-{day:02d}": {
            "bearish_entry_context_trades": 2,
            "bearish_PE": {"trades": 1, "net_pnl_inr": 100.0},
            "bearish_CE": {"trades": 1, "net_pnl_inr": -50.0},
        }
        for day in range(5, 15)
    }
    result = _gate(
        target_sessions=int(VALIDATION_GATE["expected_target_sessions"]),
        bearish_pe=pe,
        bearish_ce=ce,
        daily=daily,
    )
    assert result["status"] == "PASS_FRESH_HOLDOUT"


def test_gate_is_inconclusive_when_coverage_is_insufficient():
    result = _gate(
        target_sessions=10,
        bearish_pe=[_trade(100.0, True)],
        bearish_ce=[_trade(-50.0, False)],
        daily={},
    )
    assert result["status"] == "INCONCLUSIVE_COVERAGE"


def test_partial_holdout_does_not_score_outcomes():
    market = {
        "protocol_version": PROTOCOL_VERSION,
        "strategy_outcomes_scored": False,
        "daily_contracts": [
            {
                "date": "2026-10-05",
                "month": "2026-10",
                "spot_0915_open": 100.0,
                "strike": 100,
                "expiry": "2026-10-06",
            }
        ],
        "spot_rows": [],
        "option_rows_1m": [],
    }
    report = analyze(market, source_market_sha256="partial")
    assert report["decision"] == "HOLDOUT_NOT_COMPLETE_NO_OUTCOMES_SCORED"
    assert "primary_bearish_analysis" not in report
    assert GUARDRAILS["no_partial_holdout_outcome_reporting"] is True


def test_late_entry_forward_diagnostic_is_descriptive_only():
    assert FROZEN_LATE_ENTRY_DIAGNOSTIC_CUTOFF == "14:30"
    assert GUARDRAILS["late_entry_forward_reporting_descriptive_only"] is True
    assert GUARDRAILS["no_late_entry_hard_block"] is True
