from services.historical.strategy_f5_nifty_futures_confirmation import (
    _futures_state,
    _sign,
)
from services.historical.strategy_f5_nifty_futures_confirmation_market import (
    _nearest_expiry,
)
from services.historical.strategy_f5_nifty_futures_confirmation_protocol import (
    GUARDRAILS,
    MONTHLY_FUTURES_EXPIRIES,
    PROTOCOL_VERSION,
    STRICT_BUILD_CONFIRMATION,
)
from datetime import date


def _row(ts, *, price, oi, volume=100):
    return {
        "timestamp": f"2026-07-03T{ts}:00+05:30",
        "date": "2026-07-03",
        "expiry": "2026-07-28",
        "open": price,
        "high": price + 1,
        "low": price - 1,
        "close": price,
        "open_interest": oi,
        "volume": volume,
    }


def _spot(ts, price):
    return {
        "timestamp": f"2026-07-03T{ts}:00+05:30",
        "date": "2026-07-03",
        "open": price,
        "high": price + 1,
        "low": price - 1,
        "close": price,
    }


def test_protocol_freezes_standard_futures_states_without_thresholds():
    assert PROTOCOL_VERSION == "STRATEGY_F5_NIFTY_FUTURES_CONFIRMATION_V1"
    assert MONTHLY_FUTURES_EXPIRIES == [
        "2026-07-28",
        "2026-08-25",
        "2026-09-29",
        "2026-10-27",
    ]
    assert STRICT_BUILD_CONFIRMATION == {
        "BULLISH": "LONG_BUILDUP",
        "BEARISH": "SHORT_BUILDUP",
    }
    assert GUARDRAILS["no_oi_threshold_search"] is True
    assert GUARDRAILS["no_basis_threshold_search"] is True
    assert GUARDRAILS["live_execution"] is False


def test_nearest_monthly_futures_expiry():
    assert _nearest_expiry(date(2026, 7, 1)).isoformat() == "2026-07-28"
    assert _nearest_expiry(date(2026, 7, 29)).isoformat() == "2026-08-25"
    assert _nearest_expiry(date(2026, 8, 26)).isoformat() == "2026-09-29"
    assert _nearest_expiry(date(2026, 9, 30)).isoformat() == "2026-10-27"


def test_short_buildup_is_price_down_oi_up_and_basis_is_reported():
    futures = [
        _row("09:15", price=101.0, oi=1000),
        _row("15:20", price=98.0, oi=1200),
    ]
    spot = [
        _spot("09:15", 100.0),
        _spot("15:20", 97.5),
    ]
    result = _futures_state("2026-07-03", futures, spot)
    assert result["state"] == "SHORT_BUILDUP"
    assert result["price_direction"] == "DOWN"
    assert result["oi_direction"] == "UP"
    assert result["opening_basis_points"] == 1.0
    assert result["basis_1520_points"] == 0.5
    assert result["basis_change_points"] == -0.5


def test_other_classic_price_oi_states():
    spot = [_spot("09:15", 100.0), _spot("15:20", 100.0)]
    cases = [
        (101.0, 102.0, 1000, 1200, "LONG_BUILDUP"),
        (101.0, 102.0, 1200, 1000, "SHORT_COVERING"),
        (101.0, 98.0, 1200, 1000, "LONG_UNWINDING"),
    ]
    for p0, p1, oi0, oi1, expected in cases:
        result = _futures_state(
            "2026-07-03",
            [_row("09:15", price=p0, oi=oi0), _row("15:20", price=p1, oi=oi1)],
            spot,
        )
        assert result["state"] == expected


def test_sign_has_no_magnitude_threshold():
    assert _sign(0.000001) == "UP"
    assert _sign(-0.000001) == "DOWN"
    assert _sign(0.0) == "FLAT"
