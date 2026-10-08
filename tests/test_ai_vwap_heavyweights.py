"""Verify real traded-futures VWAP, full-session bars and Kite-heavyweights batching."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from libs.contracts.models import Candle
from libs.market_time import IST
from services.ai_context.service import AIContextService
from services.broker_gateway.zerodha_kite_adapter import ZerodhaKiteAdapter
from services.historical.service import HistoricalService


class FuturesKite:
    is_active = True

    def __init__(self):
        self.calls = 0
        self.gap = False

    async def resolve_nearest_future(self, underlying):
        assert underlying == "NIFTY"
        return {"expiry": "2026-10-29"}

    async def fetch_historical_candles_window(self, instrument_id, interval, start_time, end_time):
        self.calls += 1
        assert instrument_id == "INST-NIFTY-FUT-2026-10-29"
        assert interval == "5m"
        rows = []
        current = start_time
        while current < end_time:
            index = len(rows)
            rows.append(Candle(
                instrument_id=instrument_id, interval="5m",
                start_time=current, end_time=current + timedelta(minutes=5),
                open=25000 + index, high=25003 + index,
                low=24997 + index, close=25000 + index,
                volume=100 * (index + 1), source="KITE",
            ))
            current += timedelta(minutes=5)
        return rows[1:] if self.gap else rows


@pytest.mark.asyncio
async def test_futures_vwap_uses_full_session_and_one_cached_history_call(monkeypatch):
    now = datetime(2026, 10, 9, 10, 6, tzinfo=IST).astimezone(timezone.utc)
    monkeypatch.setattr("services.historical.service.utc_now", lambda: now)
    kite = FuturesKite()
    service = HistoricalService(broker_gateway=SimpleNamespace(kite_adapter=kite))
    first = await service.get_nifty_futures_session_vwap()
    assert first["available"] is True
    assert first["basis"] == "NIFTY_FUTURES"
    assert first["instrument_id"] == "INST-NIFTY-FUT-2026-10-29"
    assert first["candle_count"] == 9
    assert first["value"] is not None
    await service.get_nifty_futures_session_vwap()
    assert kite.calls == 1


@pytest.mark.asyncio
async def test_futures_vwap_refuses_gapped_session_without_guessing(monkeypatch):
    now = datetime(2026, 10, 9, 10, 6, tzinfo=IST).astimezone(timezone.utc)
    monkeypatch.setattr("services.historical.service.utc_now", lambda: now)
    kite = FuturesKite()
    kite.gap = True
    result = await HistoricalService(
        broker_gateway=SimpleNamespace(kite_adapter=kite)
    ).get_nifty_futures_session_vwap()
    assert result["value"] is None
    assert result["reason"] == "INCOMPLETE_FUTURES_SESSION_CANDLES"


@pytest.mark.asyncio
async def test_ai_technicals_labels_futures_basis_not_index_vwap():
    at = datetime(2026, 10, 9, 10, 5, tzinfo=IST).astimezone(timezone.utc)

    class Historical:
        async def get_candles(self, **kwargs):
            return [
                Candle(
                    instrument_id="INST-NIFTY-INDEX", interval="5m",
                    start_time=at - timedelta(minutes=5 * (61 - i)),
                    end_time=at - timedelta(minutes=5 * (60 - i)),
                    open=24000, high=24003, low=23997, close=24000,
                    volume=0, source="KITE",
                )
                for i in range(60)
            ]

        async def get_nifty_futures_session_vwap(self):
            return {
                "available": True, "basis": "NIFTY_FUTURES",
                "value": 24123.45,
                "instrument_id": "INST-NIFTY-FUT-2026-10-29",
                "data_through": at.isoformat(), "candle_count": 10,
            }

    service = AIContextService(
        market_data_service=None, historical_service=Historical(),
        option_chain_service=None, portfolio_service=None,
        broker_gateway=None, broker_session_service=None, risk_service=None,
    )
    metrics = (await service.get_technicals(interval="5m"))["metrics"]
    assert metrics["session_vwap"] == 24123.45
    assert metrics["session_vwap_basis"] == "NIFTY_FUTURES"
    assert metrics["session_vwap_instrument_id"] == "INST-NIFTY-FUT-2026-10-29"


class HeavyweightsClient:
    def __init__(self):
        self.calls = []
        self.at = datetime.now(timezone.utc)

    def quote(self, symbols):
        self.calls.append(symbols)
        return {
            key: {
                "last_price": 1700.0, "ohlc": {"close": 1600.0},
                "timestamp": self.at,
            }
            for key in symbols
        }


@pytest.mark.asyncio
async def test_kite_heavyweights_are_one_batched_cached_quote():
    client = HeavyweightsClient()
    kite = ZerodhaKiteAdapter(custom_client=client)
    kite._access_token = "test-token"
    first = await kite.get_heavyweights_quotes()
    again = await kite.get_heavyweights_quotes()
    assert first["available"] is True
    assert len(first["stocks"]) == 5
    assert all(stock["source"] == "KITE" for stock in first["stocks"])
    assert first["stocks"][0]["change_pct"] == 6.25
    assert len(client.calls) == 1
    assert client.calls[0] == [
        "NSE:HDFCBANK", "NSE:RELIANCE", "NSE:ICICIBANK", "NSE:INFY", "NSE:TCS"
    ]
    assert first == again
