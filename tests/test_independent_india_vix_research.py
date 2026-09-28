from datetime import datetime
from zoneinfo import ZoneInfo

from services.historical.independent_india_vix_research import (
    _chunks,
    build_vix_dataset,
)

IST = ZoneInfo("Asia/Kolkata")


class FakeVixClient:
    source = "KITE"

    def resolve_india_vix_token(self):
        return "12345"

    def history(
        self,
        instrument_token,
        start,
        end,
        *,
        instrument_name,
        include_oi,
    ):
        assert instrument_token == "12345"
        assert instrument_name == "NSE:INDIA VIX"
        assert include_oi is False
        rows = []
        day = start.date()
        if day <= end.date():
            rows.append(
                {
                    "timestamp": datetime(
                        day.year, day.month, day.day, 9, 15, tzinfo=IST
                    ).isoformat(),
                    "open": 12.0,
                    "high": 12.2,
                    "low": 11.9,
                    "close": 12.1,
                    "volume": 0.0,
                    "open_interest": None,
                    "instrument": "NSE:INDIA VIX",
                    "source": "KITE",
                }
            )
        return rows


def test_vix_dataset_is_development_attribution_only():
    source = {"session_dates": ["2026-05-19"]}
    result = build_vix_dataset(source, FakeVixClient())
    assert result["research_type"] == "INDEPENDENT_INDIA_VIX_ATTRIBUTION_DATA"
    assert result["research_only"] is True
    assert result["interval_minutes"] == 5
    assert result["instrument_token"] == "12345"
    assert result["quality"]["sessions"] == 1
    assert result["quality"]["rows"] == 1
    assert result["quality"]["duplicate_rows"] == 0
    assert result["quality"]["invalid_ohlc_rows"] == 0


def test_vix_requests_are_chunked_by_ten_frozen_sessions():
    values = [
        datetime(2026, 5, day, tzinfo=IST).date()
        for day in range(1, 24)
    ]
    chunks = _chunks(values)
    assert [len(chunk) for chunk in chunks] == [10, 10, 3]
