"""Focused tests for Kite selection and normalized order translation."""

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
