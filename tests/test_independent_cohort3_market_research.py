from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from services.historical.independent_cohort3_market_research import collect
from services.historical.independent_cohort3_protocol import (
    EXPECTED,
    FUTURES_MONTHLY_EXPIRIES,
    SESSION_DATES,
)

IST = ZoneInfo("Asia/Kolkata")


class FakeBreezeFuturesClient:
    def __init__(self):
        self.calls = []
        self.last_history_debug = {}

    def history(self, session_dates, expiry_date):
        self.calls.append(
            ([day.isoformat() for day in session_dates], str(expiry_date))
        )
        rows = []
        for day in session_dates:
            start = datetime.combine(
                day, datetime.strptime("09:15", "%H:%M").time(), tzinfo=IST
            )
            for offset in range(75):
                ts = start + timedelta(minutes=5 * offset)
                price = 25000.0 + offset
                rows.append({
                    "timestamp": ts.isoformat(),
                    "open": price,
                    "high": price + 1.0,
                    "low": price - 1.0,
                    "close": price + 0.25,
                    "volume": 1000.0 + offset,
                    "open_interest": 500000.0 + offset,
                    "instrument": f"NIFTY FUT {expiry_date}",
                    "source": "BREEZE",
                })
        self.last_history_debug = {
            "expiry_date": str(expiry_date),
            "requests": [
                {"date": day.isoformat(), "status": 200, "error": None, "raw_count": 75}
                for day in session_dates
            ],
            "normalized_count": len(rows),
        }
        return rows


def test_cohort3_market_collector_uses_only_frozen_dates_and_expiries():
    client = FakeBreezeFuturesClient()
    report = collect(client)

    requested_dates = [
        day for dates, _expiry in client.calls for day in dates
    ]
    requested_expiries = sorted({expiry for _dates, expiry in client.calls})

    assert sorted(requested_dates) == sorted(SESSION_DATES)
    assert len(requested_dates) == len(SESSION_DATES)
    assert requested_expiries == sorted(FUTURES_MONTHLY_EXPIRIES)
    assert min(requested_dates) == "2026-01-02"
    assert max(requested_dates) == "2026-05-05"
    assert all(day >= "2026-01-02" for day in requested_dates)

    assert report["session_dates"] == SESSION_DATES
    assert report["coverage"]["futures_dates"] == SESSION_DATES
    assert report["coverage"]["nifty_futures_rows"] == EXPECTED["five_minute_rows"]
    assert len(report["canonical_market_rows"]) == EXPECTED["five_minute_rows"]
    assert report["quality"]["complete_75_bar_sessions"] == EXPECTED["sessions"]
    assert report["quality"]["missing_volume_rows"] == 0
    assert report["quality"]["missing_open_interest_rows"] == 0
    assert report["blind_data_used"] is False
    assert report["implementation_allowed"] is False
