from datetime import date, datetime

import httpx

from services.historical.independent_market_research import (
    Candle,
    Instrument,
    PublicNseChartClient,
    _last_complete_dates,
    _normalize_candles,
)


def _raw(ts_ms: int, *, volume: int = 100, oi: int | None = None):
    row = {
        "time": ts_ms,
        "open": 100,
        "high": 102,
        "low": 99,
        "close": 101,
        "volume": volume,
    }
    if oi is not None:
        row["openInterest"] = oi
    return row


def test_normalize_public_chart_candle_preserves_volume_oi_and_provenance():
    # NSE charting labels the 09:15-09:20 candle as exchange wall time 09:19:59.
    ts = int(datetime.fromisoformat("2026-09-01T09:19:59+00:00").timestamp() * 1000)
    instrument = Instrument("NIFTY26SEPFUT", "123", "Futures", "FO")

    rows = _normalize_candles([_raw(ts, volume=4321, oi=9876)], instrument)

    assert len(rows) == 1
    candle = rows[0]
    assert candle.timestamp == "2026-09-01T09:15:00+05:30"
    assert candle.volume == 4321
    assert candle.open_interest == 9876
    assert candle.source == "NSE_PUBLIC_CHART"
    assert candle.instrument == "NIFTY26SEPFUT"


def test_normalize_filters_outside_regular_session():
    before = int(datetime.fromisoformat("2026-09-01T09:14:59+00:00").timestamp() * 1000)
    inside = int(datetime.fromisoformat("2026-09-01T09:19:59+00:00").timestamp() * 1000)
    after = int(datetime.fromisoformat("2026-09-01T15:34:59+00:00").timestamp() * 1000)
    instrument = Instrument("NIFTY 50", "26000", "Index", "IDX")

    rows = _normalize_candles([_raw(before), _raw(inside), _raw(after)], instrument)

    assert [row.timestamp for row in rows] == ["2026-09-01T09:15:00+05:30"]


def test_last_complete_dates_requires_all_75_five_minute_starts():
    rows: list[Candle] = []
    for day in (date(2026, 9, 1), date(2026, 9, 2)):
        start = datetime.fromisoformat(f"{day.isoformat()}T09:15:00+05:30")
        count = 75 if day.day == 1 else 74
        for idx in range(count):
            ts = start.timestamp() + idx * 300
            rows.append(
                Candle(
                    timestamp=datetime.fromtimestamp(ts, tz=start.tzinfo).isoformat(),
                    open=100,
                    high=101,
                    low=99,
                    close=100,
                    volume=1,
                    open_interest=None,
                    source="NSE_PUBLIC_CHART",
                    instrument="NIFTY 50",
                    instrument_type="Index",
                )
            )

    assert _last_complete_dates(rows, 10) == [date(2026, 9, 1)]


def test_client_search_and_history_use_public_chart_protocol():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host == "www.nseindia.com":
            return httpx.Response(200, text="ok")
        if request.url.path.endswith("symbolsDynamic"):
            return httpx.Response(
                200,
                json={
                    "status": True,
                    "data": [
                        {
                            "symbol": "NIFTY 50",
                            "scripcode": "26000",
                            "type": "Index",
                            "exchange": "NSE",
                            "description": "NIFTY 50",
                        }
                    ],
                },
            )
        if request.url.path.endswith("symbolHistoricalData"):
            ts = int(datetime.fromisoformat("2026-09-01T09:19:59+00:00").timestamp() * 1000)
            return httpx.Response(200, json={"status": True, "data": [_raw(ts, volume=0)]})
        raise AssertionError(str(request.url))

    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    client = PublicNseChartClient(http)

    instrument = client.resolve_exact("NIFTY 50", "IDX")
    rows = client.history(
        instrument,
        datetime.fromisoformat("2026-09-01T00:00:00+05:30"),
        datetime.fromisoformat("2026-09-02T00:00:00+05:30"),
    )

    assert instrument.token == "26000"
    assert len(rows) == 1
    assert rows[0].volume == 0
    assert any(request.url.path.endswith("symbolsDynamic") for request in calls)
    assert any(request.url.path.endswith("symbolHistoricalData") for request in calls)


def test_normalize_last_five_minute_bar_maps_152959_to_1525_start():
    ts = int(datetime.fromisoformat("2026-09-01T15:29:59+00:00").timestamp() * 1000)
    instrument = Instrument("NIFTY 50", "26000", "Index", "IDX")

    rows = _normalize_candles([_raw(ts, volume=0)], instrument)

    assert len(rows) == 1
    assert rows[0].timestamp == "2026-09-01T15:25:00+05:30"


def test_client_chunks_long_intraday_history_requests_and_deduplicates():
    history_calls: list[dict] = []
    boundary = int(datetime.fromisoformat("2026-09-06T09:19:59+00:00").timestamp() * 1000)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.nseindia.com":
            return httpx.Response(200, text="ok")
        if request.url.path.endswith("symbolHistoricalData"):
            payload = __import__("json").loads(request.content.decode())
            history_calls.append(payload)
            return httpx.Response(
                200,
                json={"status": True, "data": [_raw(boundary, volume=10)]},
            )
        raise AssertionError(str(request.url))

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = PublicNseChartClient(http)
    instrument = Instrument("NIFTY 50", "26000", "Index", "IDX")

    rows = client.history(
        instrument,
        datetime.fromisoformat("2026-09-01T00:00:00+05:30"),
        datetime.fromisoformat("2026-09-13T00:00:00+05:30"),
    )

    assert len(history_calls) == 3
    assert len(rows) == 1
    assert client.last_history_debug["chunk_count"] == 3
    assert all(
        call["toDate"] - call["fromDate"] <= 5 * 24 * 60 * 60
        for call in history_calls
    )
