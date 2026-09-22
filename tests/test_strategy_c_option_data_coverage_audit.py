from services.historical.strategy_c_option_data_coverage_audit import (
    _candle_flags,
    _rank_option_contracts,
    _signal_result,
)
from datetime import datetime, timezone


def _signal(direction: str = "CALL") -> dict:
    return {
        "signal_id": "S1",
        "date": "2026-01-05",
        "direction": direction,
        "entry_time": "2026-01-05T04:31:00+00:00",
        "entry_time_ist": "2026-01-05T10:01:00+05:30",
        "underlying_entry_price": 25020.0,
    }


def _meta(
    instrument_id: str,
    *,
    expiry: str,
    strike: float,
    right: str = "CALL",
    valid_from: str | None = None,
    valid_to: str | None = None,
) -> dict:
    return {
        "instrument_id": instrument_id,
        "broker": "ICICI_BREEZE",
        "exchange": "NFO",
        "segment": "OPTIONS",
        "underlying": "NIFTY",
        "stock_code": instrument_id,
        "expiry": expiry,
        "strike": strike,
        "option_right": right,
        "lot_size": 25,
        "tick_size": 0.05,
        "broker_token": None,
        "tradable": 1,
        "valid_from": valid_from,
        "valid_to": valid_to,
    }


def test_rank_contracts_uses_static_expiry_and_strike_only():
    rows = [
        _meta("FAR", expiry="2026-01-08", strike=25100),
        _meta("NEAR", expiry="2026-01-08", strike=25000),
        _meta("NEXT", expiry="2026-01-15", strike=25000),
        _meta("EXPIRED", expiry="2026-01-01", strike=25000),
        _meta("WRONG_RIGHT", expiry="2026-01-08", strike=25000, right="PUT"),
    ]
    ranked = _rank_option_contracts(rows, _signal(), max_expiries=2, strikes_per_expiry=2)
    assert [row["instrument_id"] for row in ranked] == ["NEAR", "FAR", "NEXT"]
    assert ranked[0]["coverage_proxy_rank"] == 1
    assert ranked[0]["strike_distance_points"] == 20.0


def test_rank_contracts_respects_known_validity_bounds():
    rows = [
        _meta(
            "FUTURE_METADATA",
            expiry="2026-01-08",
            strike=25000,
            valid_from="2026-01-06T00:00:00+00:00",
        ),
        _meta(
            "VALID",
            expiry="2026-01-08",
            strike=25050,
            valid_from="2025-12-01T00:00:00+00:00",
            valid_to="2026-01-08T23:59:00+00:00",
        ),
    ]
    ranked = _rank_option_contracts(rows, _signal(), max_expiries=1, strikes_per_expiry=5)
    assert [row["instrument_id"] for row in ranked] == ["VALID"]
    assert ranked[0]["temporal_bounds_status"] == "BOTH_BOUNDS_PRESENT"


def test_candle_flags_distinguish_signal_close_and_post_signal_bar():
    entry = datetime(2026, 1, 5, 4, 31, tzinfo=timezone.utc)
    rows = [
        {
            "start_time": "2026-01-05T04:30:00+00:00",
            "end_time": "2026-01-05T04:31:00+00:00",
        },
        {
            "start_time": "2026-01-05T04:31:00+00:00",
            "end_time": "2026-01-05T04:32:00+00:00",
        },
    ]
    flags = _candle_flags(rows, entry)
    assert flags["near_signal_any"] is True
    assert flags["recent_completed_at_or_before_signal"] is True
    assert flags["exact_signal_close_candle"] is True
    assert flags["exact_post_signal_candle"] is True


def test_signal_result_never_calls_proxy_production_equivalent():
    contracts = [
        {
            **_meta("NEAR", expiry="2026-01-08", strike=25000),
            "coverage_proxy_rank": 1,
            "strike_distance_points": 20.0,
            "temporal_bounds_status": "BOUNDS_UNKNOWN",
        }
    ]
    candles = [
        {
            "instrument_id": "NEAR",
            "start_time": "2026-01-05T04:30:00+00:00",
            "end_time": "2026-01-05T04:31:00+00:00",
        },
        {
            "instrument_id": "NEAR",
            "start_time": "2026-01-05T04:31:00+00:00",
            "end_time": "2026-01-05T04:32:00+00:00",
        },
    ]
    result = _signal_result(_signal(), contracts, candles)
    assert result["coverage_state"] == "NATIVE_1M_AROUND_SIGNAL"
    assert "not production ContractSelector" in result["selection_note"]
