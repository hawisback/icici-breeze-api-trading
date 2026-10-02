from datetime import datetime, timedelta

import httpx

from services.historical.independent_market_research import (
    _candles_from_yahoo,
    _last_complete_dates,
)
from services.historical.yahoo_chart import YahooChartClient


def _chart_payload(timestamps, volumes):
    size = len(timestamps)
    return {
        "chart": {
            "result": [
                {
                    "meta": {
                        "exchangeTimezoneName": "Asia/Kolkata",
                        "dataGranularity": "5m",
                    },
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [
                            {
                                "open": [100.0] * size,
                                "high": [101.0] * size,
                                "low": [99.0] * size,
                                "close": [100.5] * size,
                                "volume": volumes,
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }


def test_yahoo_chart_converts_true_epoch_to_ist_and_preserves_volume():
    raw_ts = int(datetime.fromisoformat("2026-09-25T03:45:00+00:00").timestamp())

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chart_payload([raw_ts], [4321]))

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = YahooChartClient(http)

    rows = client.history(
        "^NSEI",
        datetime.fromisoformat("2026-09-25T00:00:00+05:30"),
        datetime.fromisoformat("2026-09-26T00:00:00+05:30"),
    )

    assert rows == [
        {
            "timestamp": "2026-09-25T09:15:00+05:30",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 4321.0,
        }
    ]
    assert client.last_history_debug["normalized_count"] == 1


def test_yahoo_75_bars_form_complete_nifty_session():
    start_utc = datetime.fromisoformat("2026-09-25T03:45:00+00:00")
    timestamps = [
        int((start_utc + timedelta(minutes=5 * idx)).timestamp())
        for idx in range(75)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_chart_payload(timestamps, [0] * 75))

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = YahooChartClient(http)
    rows = client.history(
        "^NSEI",
        datetime.fromisoformat("2026-09-25T00:00:00+05:30"),
        datetime.fromisoformat("2026-09-26T00:00:00+05:30"),
    )
    candles = _candles_from_yahoo(rows, "^NSEI", "Index")

    assert len(candles) == 75
    assert candles[0].timestamp == "2026-09-25T09:15:00+05:30"
    assert candles[-1].timestamp == "2026-09-25T15:25:00+05:30"
    assert [day.isoformat() for day in _last_complete_dates(candles, 10)] == ["2026-09-25"]
