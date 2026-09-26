from datetime import date, datetime

import httpx

from services.historical.research_provider_clients import (
    BreezeFuturesClient,
    DhanHistoricalClient,
    UpstoxHistoricalClient,
)


class _FakeBreeze:
    def __init__(self, api_key):
        self.api_key = api_key
        self.session = None
        self.calls = []

    def generate_session(self, api_secret, session_token):
        self.session = (api_secret, session_token)

    def get_historical_data_v2(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "Status": 200,
            "Error": None,
            "Success": [
                {
                    "datetime": "2026-09-25 09:15:00",
                    "open": 25000,
                    "high": 25020,
                    "low": 24990,
                    "close": 25010,
                    "volume": 12345,
                    "open_interest": 456789,
                }
            ],
        }


def test_breeze_futures_normalizes_volume_oi_and_keeps_expiry_explicit():
    created = []

    def factory(api_key):
        client = _FakeBreeze(api_key)
        created.append(client)
        return client

    client = BreezeFuturesClient("key", "secret", "session", breeze_factory=factory)
    rows = client.history([date(2026, 9, 25)], "2026-09-29")

    assert len(rows) == 1
    assert rows[0]["timestamp"] == "2026-09-25T09:15:00+05:30"
    assert rows[0]["volume"] == 12345
    assert rows[0]["open_interest"] == 456789
    assert rows[0]["instrument"] == "NIFTY FUT 2026-09-29"
    assert created[0].session == ("secret", "session")
    call = created[0].calls[0]
    assert call["interval"] == "5minute"
    assert call["product_type"] == "futures"
    assert call["expiry_date"] == "2026-09-29T07:00:00.000Z"


def test_upstox_historical_normalizes_vix_or_futures_candle():
    payload = {
        "status": "success",
        "data": {
            "candles": [
                [
                    "2026-09-25T09:15:00+05:30",
                    100,
                    102,
                    99,
                    101,
                    500,
                    700,
                ]
            ]
        },
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer token"
        assert "NSE_INDEX%7CIndia%20VIX" in str(request.url)
        return httpx.Response(200, json=payload)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = UpstoxHistoricalClient("token", http)
    rows = client.history(
        "NSE_INDEX|India VIX",
        datetime.fromisoformat("2026-09-25T09:15:00+05:30"),
        datetime.fromisoformat("2026-09-25T15:30:00+05:30"),
    )

    assert rows == [
        {
            "timestamp": "2026-09-25T09:15:00+05:30",
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "close": 101.0,
            "volume": 500.0,
            "open_interest": 700.0,
            "instrument": "NSE_INDEX|India VIX",
            "source": "UPSTOX",
        }
    ]


def test_dhan_futures_normalizes_true_epoch_and_oi():
    raw_ts = int(datetime.fromisoformat("2026-09-25T03:45:00+00:00").timestamp())

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["access-token"] == "token"
        body = __import__("json").loads(request.content.decode())
        assert body["exchangeSegment"] == "NSE_FNO"
        assert body["instrument"] == "FUTIDX"
        assert body["interval"] == "5"
        assert body["oi"] is True
        return httpx.Response(
            200,
            json={
                "timestamp": [raw_ts],
                "open": [25000],
                "high": [25020],
                "low": [24990],
                "close": [25010],
                "volume": [12345],
                "open_interest": [456789],
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = DhanHistoricalClient("token", http)
    rows = client.history(
        "99999",
        datetime.fromisoformat("2026-09-25T09:15:00+05:30"),
        datetime.fromisoformat("2026-09-25T15:30:00+05:30"),
    )

    assert len(rows) == 1
    assert rows[0]["timestamp"] == "2026-09-25T09:15:00+05:30"
    assert rows[0]["volume"] == 12345.0
    assert rows[0]["open_interest"] == 456789.0
    assert rows[0]["source"] == "DHAN"
