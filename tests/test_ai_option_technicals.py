"""Contract-premium option technical indicators are never computed on index/futures candles."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from libs.contracts.models import Candle, utc_now
from services.ai_context.service import AIContextService
from services.api_gateway import ai_routes, ai_local_access
from services.broker_gateway.zerodha_kite_adapter import ZerodhaKiteAdapter

CE = "INST-NIFTY-2026-10-13-25000-CE"
PE = "INST-NIFTY-2026-10-13-25000-PE"
BAD = "INST-NIFTY-2026-10-13-25100-PE"


class KiteOptionMaster:
    def __init__(self):
        self.historical_tokens = []

    def instruments(self, exchange):
        assert exchange == "NFO"
        base = {"name": "NIFTY", "expiry": date(2026, 10, 13),
                "strike": 25000, "exchange": "NFO"}
        return [
            {**base, "instrument_type": "CE", "tradingsymbol": "CE-SYMBOL",
             "instrument_token": 70001, "lot_size": 65},
            {**base, "instrument_type": "PE", "tradingsymbol": "PE-SYMBOL",
             "instrument_token": 70002, "lot_size": 65},
            {**base, "instrument_type": "FUT", "tradingsymbol": "FUT-SYMBOL",
             "instrument_token": 80001, "lot_size": 65},
        ]

    def historical_data(self, instrument_token, from_date, to_date, interval, oi):
        self.historical_tokens.append(instrument_token)
        return [
            {"date": from_date, "open": 100.0, "high": 102.0,
             "low": 99.0, "close": 101.0, "volume": 10, "oi": 1000}
        ]


@pytest.mark.asyncio
async def test_kite_option_token_resolution_never_uses_future_token():
    client = KiteOptionMaster()
    adapter = ZerodhaKiteAdapter(custom_client=client)
    adapter._access_token = "test-token"
    assert (await adapter.resolve_option_contract(CE))["symbol"] == "CE-SYMBOL"
    assert await adapter._find_instrument_token(CE) == 70001
    assert await adapter._find_instrument_token(PE) == 70002
    assert await adapter._find_instrument_token(BAD) is None
    assert await adapter._find_instrument_token("INST-NIFTY-2026-10-99-25000-CE") is None
    candles = await adapter.fetch_historical_candles_window(
        CE, "5m", utc_now() - timedelta(days=1), utc_now()
    )
    assert len(candles) == 1
    assert candles[0].instrument_id == CE
    assert client.historical_tokens == [70001]
    assert await adapter._find_instrument_token("INST-NIFTY-FUT-2026-10-13") == 80001


def _candles(instrument_id, close):
    end = utc_now().replace(second=0, microsecond=0) - timedelta(minutes=5)
    start = end - timedelta(minutes=65 * 5)
    candles = []
    for i in range(65):
        first = start + timedelta(minutes=i * 5)
        value = close + i * 0.5
        candles.append(Candle(
            instrument_id=instrument_id, interval="5m",
            start_time=first, end_time=first + timedelta(minutes=5),
            open=value, high=value + 2, low=value - 1,
            close=value + 0.5, volume=100 + i, open_interest=1000,
            source="KITE",
        ))
    return candles


class OptionHistory:
    is_active = True

    def __init__(self):
        self.calls = []

    async def resolve_option_contract(self, instrument_id):
        if instrument_id == BAD:
            return None
        return {
            "instrument_id": instrument_id,
            "symbol": "CE-SYMBOL" if instrument_id == CE else "PE-SYMBOL",
            "expiry": "2026-10-13", "strike": 25000,
            "right": "CE" if instrument_id == CE else "PE",
            "lot_size": 65, "instrument_token": 70001 if instrument_id == CE else 70002,
        }

    async def fetch_historical_candles_window(self, *, instrument_id,
                                               interval, start_time, end_time):
        self.calls.append((instrument_id, interval, start_time, end_time))
        close = 50.0 if instrument_id == CE else 150.0
        return _candles(instrument_id, close)


@pytest.mark.asyncio
async def test_batch_uses_each_option_premium_not_underlying_index(monkeypatch):
    monkeypatch.setattr(
        "services.ai_context.service.asyncio.sleep",
        lambda _seconds: __import__("asyncio").sleep(0),
    )
    kite = OptionHistory()
    svc = AIContextService(
        market_data_service=None, historical_service=None, option_chain_service=None,
        portfolio_service=None,
        broker_gateway=SimpleNamespace(kite_adapter=kite),
        broker_session_service=None, risk_service=None,
    )
    response = await svc.get_nifty_option_technicals(
        instrument_ids=[CE, PE], interval="5m", limit=100,
    )
    assert response["requested_count"] == 2
    assert response["available_count"] == 2
    rows = response["contracts"]
    assert [r["instrument_id"] for r in rows] == [CE, PE]
    assert all(r["available"] and r["source"] == "KITE" for r in rows)
    assert all(r["basis"] == "OPTION_PREMIUM" for r in rows)
    assert rows[0]["metrics"]["ema_50"] is not None
    assert rows[0]["metrics"]["rsi_14"] is not None
    assert rows[0]["metrics"]["macd_histogram"] is not None
    assert rows[0]["metrics"]["session_vwap"] is not None
    assert rows[0]["metrics"]["latest_close"] != rows[1]["metrics"]["latest_close"]
    assert all(i == "5m" for _, i, _, _ in kite.calls)


@pytest.mark.asyncio
async def test_unlisted_contract_returns_explicit_unavailable_without_futures_fallback():
    kite = OptionHistory()
    svc = AIContextService(
        market_data_service=None, historical_service=None, option_chain_service=None,
        portfolio_service=None, broker_gateway=SimpleNamespace(kite_adapter=kite),
        broker_session_service=None, risk_service=None,
    )
    result = await svc.get_nifty_option_technicals(instrument_ids=[BAD], interval="5m")
    assert result["available_count"] == 0
    assert result["contracts"][0]["reason"] == "OPTION_NOT_LISTED_IN_KITE_NFO_MASTER"
    assert kite.calls == []


@pytest.mark.asyncio
async def test_insufficient_history_has_no_fabricated_indicators():
    kite = OptionHistory()

    async def empty_history(**kwargs):
        return _candles(CE, 100)[:10]
    kite.fetch_historical_candles_window = empty_history
    svc = AIContextService(
        market_data_service=None, historical_service=None, option_chain_service=None,
        portfolio_service=None, broker_gateway=SimpleNamespace(kite_adapter=kite),
        broker_session_service=None, risk_service=None,
    )
    result = await svc.get_nifty_option_technicals(instrument_ids=[CE], interval="5m")
    item = result["contracts"][0]
    assert not item["available"]
    assert item["reason"] == "INSUFFICIENT_COMPLETED_OPTION_CANDLES"
    assert item["metrics"]["ema_50"] is None


@pytest.mark.asyncio
async def test_route_accepts_repeated_ids_and_rejects_invalid_or_large_batches(monkeypatch):
    ai_routes._option_technicals_cache.clear()
    calls = []

    class FakeContext:
        async def get_nifty_option_technicals(self, **kwargs):
            calls.append(kwargs)
            return {"contracts": [], "requested_count": len(kwargs["instrument_ids"])}

    settings = SimpleNamespace(ai_trust_local_docker_gateway=False)
    monkeypatch.setattr(ai_local_access, "get_services", lambda: SimpleNamespace(settings=settings))
    monkeypatch.setattr(ai_routes, "_context_service", lambda: FakeContext())
    app = FastAPI()
    app.include_router(ai_routes.router)
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 9999))
    path = "/api/v1/ai/nifty/options/technicals"
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost:8000") as client:
        q = [("instrument_ids", CE), ("instrument_ids", PE),
             ("interval", "5m"), ("limit", "200")]
        result = await client.get(path, params=q)
        assert result.status_code == 200
        assert result.json()["requested_count"] == 2
        assert (await client.get(path, params=q)).status_code == 200
        assert len(calls) == 1  # short-term cache avoids broker rate-limit bursts
        duplicate = await client.get(path, params=[("instrument_ids", CE),
                                                   ("instrument_ids", CE)])
        assert duplicate.status_code == 422
        invalid = await client.get(path, params={"instrument_ids": "NIFTY_FUTURES"})
        assert invalid.status_code == 422
        too_many = await client.get(
            path, params=[("instrument_ids", f"INST-NIFTY-2026-10-13-{25000+i*50}-CE")
                          for i in range(7)]
        )
        assert too_many.status_code == 422
        wrong_interval = await client.get(path, params={"instrument_ids": CE,
                                                        "interval": "30m"})
        assert wrong_interval.status_code == 422
    ai_routes._option_technicals_cache.clear()
