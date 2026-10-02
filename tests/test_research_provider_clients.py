from datetime import date, datetime

import httpx

from services.historical.research_provider_clients import (
    BreezeFuturesClient,
    DhanHistoricalClient,
    KiteHistoricalClient,
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
    assert call["from_date"] == "2026-09-25T09:15:00.000Z"
    assert call["to_date"] == "2026-09-25T15:30:00.000Z"
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


class _FakeKite:
    def __init__(self, api_key):
        self.api_key = api_key
        self.access_token = None
        self.calls = []

    def set_access_token(self, access_token):
        self.access_token = access_token

    def instruments(self, exchange):
        if exchange == "NSE":
            return [
                {
                    "instrument_token": 264969,
                    "tradingsymbol": "INDIA VIX",
                    "name": "INDIA VIX",
                    "instrument_type": "EQ",
                }
            ]
        if exchange == "NFO":
            return [
                {
                    "instrument_token": 12345,
                    "tradingsymbol": "NIFTY26SEPFUT",
                    "name": "NIFTY",
                    "instrument_type": "FUT",
                    "expiry": date(2026, 9, 29),
                }
            ]
        return []

    def historical_data(
        self,
        instrument_token,
        from_date,
        to_date,
        interval,
        continuous=False,
        oi=False,
    ):
        self.calls.append(
            {
                "instrument_token": instrument_token,
                "from_date": from_date,
                "to_date": to_date,
                "interval": interval,
                "continuous": continuous,
                "oi": oi,
            }
        )
        return [
            {
                "date": datetime.fromisoformat("2026-09-25T09:15:00+05:30"),
                "open": 25000,
                "high": 25020,
                "low": 24990,
                "close": 25010,
                "volume": 12345,
                "oi": 456789,
            }
        ]


def test_kite_historical_normalizes_current_future_with_oi():
    created = []

    def factory(api_key):
        client = _FakeKite(api_key)
        created.append(client)
        return client

    client = KiteHistoricalClient("key", "access", kite_factory=factory)
    rows = client.history(
        "12345",
        datetime.fromisoformat("2026-09-25T09:15:00+05:30"),
        datetime.fromisoformat("2026-09-25T15:30:00+05:30"),
        instrument_name="NIFTY26SEPFUT",
        include_oi=True,
    )

    assert len(rows) == 1
    assert rows[0]["source"] == "KITE"
    assert rows[0]["volume"] == 12345.0
    assert rows[0]["open_interest"] == 456789.0
    assert created[0].access_token == "access"
    call = created[0].calls[0]
    assert call["instrument_token"] == 12345
    assert call["interval"] == "5minute"
    assert call["continuous"] is False
    assert call["oi"] is True


def test_kite_resolves_vix_and_nifty_future_from_daily_instruments():
    client = KiteHistoricalClient("key", "access", kite_factory=_FakeKite)

    assert client.resolve_india_vix_token() == "264969"
    assert client.resolve_nifty_future_token("2026-09-29") == "12345"
