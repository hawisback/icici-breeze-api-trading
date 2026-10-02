from services.historical.strategy_f5_bearish_pe_late_entry_june_holdout import (
    _entry_time_regime,
)
from services.historical.strategy_f5_bearish_pe_late_entry_june_holdout_market import (
    _nearest_expiry,
)
from services.historical.strategy_f5_bearish_pe_late_entry_june_holdout_protocol import (
    CANDIDATE_NAME,
    EXPIRIES,
    FROZEN_CUTOFF,
    GUARDRAILS,
    PROTOCOL_VERSION,
    WINDOW,
)
from datetime import date


def test_june_holdout_is_single_frozen_time_candidate():
    assert PROTOCOL_VERSION == (
        "STRATEGY_F5_BEARISH_PE_LATE_ENTRY_JUNE_HOLDOUT_V1"
    )
    assert CANDIDATE_NAME == "ENTRY_TIME_BEARISH_PE_NO_NEW_ENTRY_GE_1430"
    assert FROZEN_CUTOFF == "14:30"
    assert WINDOW["start"] == "2026-06-01"
    assert WINDOW["end"] == "2026-06-30"
    assert GUARDRAILS["no_time_cutoff_search_on_june"] is True


def test_june_expiries_are_frozen_tuesdays():
    assert EXPIRIES == [
        "2026-06-02",
        "2026-06-09",
        "2026-06-16",
        "2026-06-23",
        "2026-06-30",
    ]
    assert _nearest_expiry(date(2026, 6, 1)).isoformat() == "2026-06-02"
    assert _nearest_expiry(date(2026, 6, 30)).isoformat() == "2026-06-30"


def _spot(ts: str, close: float):
    return {
        "timestamp": ts,
        "date": ts[:10],
        "open": 100.0,
        "high": max(100.0, close),
        "low": min(100.0, close),
        "close": close,
    }


def test_entry_time_regime_uses_only_completed_bars():
    rows = [
        _spot("2026-06-01T09:15:00+05:30", 99.0),
        _spot("2026-06-01T09:20:00+05:30", 98.0),
        _spot("2026-06-01T09:25:00+05:30", 120.0),
    ]
    context = _entry_time_regime(
        rows,
        "2026-06-01T09:25:00+05:30",
    )
    assert context["available"] is True
    assert context["completed_5m_bars"] == 2
    assert context["regime"] == "BEARISH"


def test_holdout_keeps_baseline_trade_management_frozen():
    assert GUARDRAILS["keep_existing_f5_exit_and_trail_unchanged"] is True
    assert GUARDRAILS["no_stop_or_target_search_on_june"] is True
    assert GUARDRAILS["entry_time_regime_uses_no_future_information"] is True
