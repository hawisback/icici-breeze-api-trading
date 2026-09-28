from datetime import date

from services.historical.independent_nifty_spot_research import (
    BreezeNiftySpotClient,
    build_spot_dataset,
)


class FakeSpotClient:
    source = "BREEZE"

    def __init__(self):
        self.request_diagnostics = []

    def history(self, session_dates):
        rows = []
        for day in session_dates:
            rows.append(
                {
                    "timestamp": f"{day.isoformat()}T09:15:00+05:30",
                    "open": 25000.0,
                    "high": 25010.0,
                    "low": 24990.0,
                    "close": 25005.0,
                    "source": "BREEZE",
                    "instrument": "NIFTY 50",
                }
            )
        return rows


def test_spot_attribution_dataset_is_raw_research_only():
    source = {"session_dates": ["2026-05-19", "2026-05-20"]}
    result = build_spot_dataset(source, FakeSpotClient())
    assert result["research_type"] == "INDEPENDENT_NIFTY_SPOT_ATTRIBUTION_DATA"
    assert result["research_only"] is True
    assert result["interval_minutes"] == 5
    assert result["quality"]["sessions"] == 2
    assert result["quality"]["rows"] == 2
    assert result["quality"]["duplicate_rows"] == 0
    assert result["quality"]["invalid_ohlc_rows"] == 0


def test_spot_request_chunks_do_not_span_more_than_nine_days():
    dates = [
        date(2026, 5, 19),
        date(2026, 5, 20),
        date(2026, 5, 29),
        date(2026, 6, 1),
    ]
    chunks = BreezeNiftySpotClient._request_chunks(dates)
    assert chunks == [
        [date(2026, 5, 19), date(2026, 5, 20)],
        [date(2026, 5, 29), date(2026, 6, 1)],
    ]
