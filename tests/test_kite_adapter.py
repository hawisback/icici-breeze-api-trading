"""Focused tests for Kite selection and normalized order translation."""

from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import SecretStr

from libs.broker_models.adapter import BrokerOrderRequest
from libs.config.settings import BrokerBackend, PlatformSettings
from libs.contracts.models import TradingMode
from services.broker_gateway.service import BrokerGatewayService
from services.broker_gateway.zerodha_kite_adapter import ZerodhaKiteAdapter


class FakeKite:
    def __init__(self):
        self.access_token = None
        self.placed = None

    def generate_session(self, request_token, api_secret):
        assert request_token == "request-token"
        assert api_secret == "api-secret"
        return {"access_token": "access-token"}

    def set_access_token(self, token):
        self.access_token = token

    def place_order(self, **kwargs):
        self.placed = kwargs
        return "KITE-ORDER-1"


@pytest.mark.asyncio
async def test_kite_adapter_authenticates_and_places_normalized_order():
    client = FakeKite()
    adapter = ZerodhaKiteAdapter(custom_client=client)

    assert await adapter.authenticate("api-key", SecretStr("api-secret"), "request-token") is True
    response = await adapter.place_order(
        BrokerOrderRequest(
            client_order_id="client-1",
            stock_code="NIFTY26SEP25000CE",
            exchange_code="NFO",
            product="options",
            action="buy",
            order_type="limit",
            quantity=25,
            price=125.5,
        )
    )

    assert response.success is True
    assert response.broker_order_id == "KITE-ORDER-1"
    assert client.placed["exchange"] == "NFO"
    assert client.placed["tradingsymbol"] == "NIFTY26SEP25000CE"
    assert client.placed["transaction_type"] == "BUY"
    assert client.placed["order_type"] == "LIMIT"


def test_gateway_selects_kite_for_live_mode(tmp_path):
    settings = PlatformSettings(
        _env_file=None,
        data_root=tmp_path,
        broker_backend=BrokerBackend.KITE,
        default_trading_mode=TradingMode.PAPER,
    )
    gateway = BrokerGatewayService(settings=settings)
    assert gateway.get_adapter(TradingMode.LIVE) is gateway.kite_adapter
    assert gateway.active_broker_name == "kite"


class FakeKiteMarket(FakeKite):
    def instruments(self, exchange):
        assert exchange == "NFO"
        return [
            {"name": "NIFTY", "instrument_type": "FUT", "expiry": date(2026, 9, 29),
             "tradingsymbol": "NIFTY26SEPFUT", "instrument_token": 9001, "lot_size": 65, "tick_size": 0.05},
            {"name": "NIFTY", "instrument_type": "FUT", "expiry": date(2026, 10, 27),
             "tradingsymbol": "NIFTY26OCTFUT", "instrument_token": 9002, "lot_size": 65, "tick_size": 0.05},
            {"name": "NIFTY", "instrument_type": "CE", "expiry": date.today() + timedelta(days=1),
             "tradingsymbol": "NIFTY26SEP23400CE", "instrument_token": 9101, "lot_size": 65, "tick_size": 0.05},
        ]

    def historical_data(self, instrument_token, from_date, to_date, interval, oi):
        assert instrument_token == 9001
        return [{"date": datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc),
                 "open": 100, "high": 102, "low": 99, "close": 101, "volume": 10, "oi": 1000}]


@pytest.mark.asyncio
async def test_kite_resolves_exact_active_future_from_instrument_master():
    adapter = ZerodhaKiteAdapter(custom_client=FakeKiteMarket())
    adapter._access_token = "access-token"
    contract = await adapter.resolve_nearest_future("NIFTY")
    assert contract and contract["expiry"] == "2026-09-29"
    assert contract["broker_token"] == "9001"
    assert contract["lot_size"] == 65
    expected_option_expiry = (date.today() + timedelta(days=1)).isoformat()
    assert await adapter.get_option_expiries("NIFTY") == [expected_option_expiry]
    candles = await adapter.fetch_historical_candles("INST-NIFTY-FUT-2026-09-29", "15m", 1)
    assert candles and candles[0].source == "KITE"
    assert await adapter._find_instrument_token("INST-NIFTY-FUT-2026-09-28") is None


class FakeKiteHistoricalMaster(FakeKite):
    def instruments(self, exchange):
        assert exchange == "NFO"
        return [
            {"name": "NIFTY", "instrument_type": "FUT", "expiry": date(2026, 8, 25),
             "tradingsymbol": "NIFTY26AUGFUT", "instrument_token": 8001, "lot_size": 65, "tick_size": 0.05},
            {"name": "NIFTY", "instrument_type": "FUT", "expiry": date(2026, 9, 29),
             "tradingsymbol": "NIFTY26SEPFUT", "instrument_token": 9001, "lot_size": 65, "tick_size": 0.05},
        ]

    def historical_data(self, instrument_token, from_date, to_date, interval, oi):
        return [{"date": from_date, "open": 100, "high": 102, "low": 99, "close": 101, "volume": 10, "oi": 1000}]


@pytest.mark.asyncio
async def test_kite_exact_historical_window_can_use_explicit_old_future_from_cached_master():
    adapter = ZerodhaKiteAdapter(custom_client=FakeKiteHistoricalMaster())
    adapter._access_token = "access-token"
    start = datetime(2026, 8, 20, 4, 0, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)
    candles = await adapter.fetch_historical_candles_window(
        "INST-NIFTY-FUT-2026-08-25", "15m", start, end
    )
    assert candles and candles[0].source == "KITE"
    assert await adapter._find_instrument_token("INST-NIFTY-FUT-2026-08-25") == 8001
