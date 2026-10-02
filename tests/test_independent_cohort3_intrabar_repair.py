from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import services.historical.independent_cohort3_intrabar_repair as repair_mod
from services.historical.independent_cohort3_intrabar_repair import (
    _day_is_complete,
    _expected_timestamps,
    repair,
)

IST = ZoneInfo("Asia/Kolkata")


def _rows(day, expiry, count=375):
    start = datetime.combine(
        date.fromisoformat(day),
        datetime.strptime("09:15", "%H:%M").time(),
        tzinfo=IST,
    )
    rows = []
    for offset in range(count):
        ts = start + timedelta(minutes=offset)
        price = 25000.0 + offset / 10.0
        rows.append({
            "timestamp": ts.isoformat(),
            "open": price,
            "high": price + 1.0,
            "low": price - 1.0,
            "close": price + 0.25,
            "volume": 1000.0 + offset,
            "open_interest": 500000.0 + offset,
            "expiry": expiry,
            "instrument": f"NIFTY FUT {expiry}",
            "source": "BREEZE",
        })
    return rows


def test_complete_day_requires_exact_375_expected_minutes():
    day = "2026-01-27"
    expiry = "2026-01-27"
    full = _rows(day, expiry)
    assert len(_expected_timestamps(day)) == 375
    assert _day_is_complete(full, day=day, expiry=expiry) is True
    assert _day_is_complete(full[:-1], day=day, expiry=expiry) is False

    shifted = [dict(row) for row in full]
    shifted[-1]["timestamp"] = "2026-01-27T15:30:00+05:30"
    assert _day_is_complete(shifted, day=day, expiry=expiry) is False


class _FakeBreeze:
    def __init__(self, full_by_day):
        self.full_by_day = full_by_day
        self.calls = []

    def get_historical_data_v2(self, **kwargs):
        day = kwargs["from_date"][:10]
        self.calls.append(day)
        success = []
        for row in self.full_by_day[day]:
            success.append({
                "datetime": row["timestamp"],
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
                "open_interest": row["open_interest"],
            })
        return {"Status": 200, "Error": None, "Success": success}


def test_repair_refetches_only_deficient_session_without_fabrication(monkeypatch):
    days = ["2026-01-27", "2026-01-28"]
    expiries = {
        "2026-01-27": "2026-01-27",
        "2026-01-28": "2026-02-24",
    }
    monkeypatch.setattr(repair_mod, "SESSION_DATES", days)
    monkeypatch.setattr(repair_mod, "validate_market", lambda _payload: None)
    monkeypatch.setattr(repair_mod, "validate_intrabar", lambda _payload: None)

    market = {
        "session_dates": days,
        "canonical_market_rows": [
            {
                "timestamp": f"{day}T09:15:00+05:30",
                "futures_instrument": f"NIFTY FUT {expiries[day]}",
            }
            for day in days
        ],
    }
    existing = {
        "session_dates": days,
        "rows": (
            _rows(days[0], expiries[days[0]], 374)
            + _rows(days[1], expiries[days[1]], 375)
        ),
        "request_diagnostics": [],
    }
    fake = _FakeBreeze({
        days[0]: _rows(days[0], expiries[days[0]], 375),
        days[1]: _rows(days[1], expiries[days[1]], 375),
    })

    report = repair(
        market,
        existing,
        fake,
        attempts_per_session=1,
        throttle_seconds=0.0,
    )

    assert fake.calls == [days[0]]
    assert report["repair"]["initial_deficient_sessions"] == [days[0]]
    assert report["repair"]["repaired_sessions"] == [days[0]]
    assert report["repair"]["remaining_deficient_sessions"] == []
    assert report["quality"]["rows"] == 750
    assert report["quality"]["complete_375_bar_sessions"] == 2
    assert report["quality"]["failed_requests"] == 0
    assert len(report["rows"]) == 750
