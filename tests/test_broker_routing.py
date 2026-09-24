"""Regression tests for independent execution-broker and data-provider routing."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from libs.config.settings import (
    BrokerBackend,
    MarketDataBackend,
    PlatformSettings,
)
from libs.contracts.models import TradingMode
from services.broker_gateway.service import BrokerGatewayService
from services.broker_session.repository import BrokerSessionRepository
from services.broker_session.service import BrokerSessionService


def _hybrid_settings(*, execution: BrokerBackend) -> PlatformSettings:
    return PlatformSettings(
        _env_file=None,
        market_data_backend=MarketDataBackend.HYBRID,
        broker_backend=execution,
        frequent_data_broker=BrokerBackend.KITE,
        reference_data_broker=BrokerBackend.BREEZE,
        breeze_api_key="breeze-key",
        breeze_secret_key="breeze-secret",
        kite_api_key="kite-key",
        kite_api_secret="kite-secret",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("execution", "expected_execution"),
    [
        (BrokerBackend.BREEZE, "breeze"),
        (BrokerBackend.KITE, "kite"),
    ],
)
async def test_hybrid_data_routing_is_independent_of_execution_broker(
    execution,
    expected_execution,
):
    paper = SimpleNamespace(place_order=AsyncMock(return_value="paper"))
    breeze = SimpleNamespace(
        place_order=AsyncMock(return_value="breeze"),
        client_manager=SimpleNamespace(is_active=True),
    )
    kite = SimpleNamespace(
        place_order=AsyncMock(return_value="kite"),
        is_active=True,
    )
    gateway = BrokerGatewayService(
        paper_adapter=paper,
        breeze_adapter=breeze,
        kite_adapter=kite,
        settings=_hybrid_settings(execution=execution),
    )

    assert gateway.execution_broker_name == expected_execution
    assert gateway.frequent_data_broker_name == "kite"
    assert gateway.reference_data_broker_name == "breeze"
    assert gateway.frequent_data_adapter is kite
    assert gateway.reference_data_adapter is breeze

    result = await gateway.place_order(object(), mode=TradingMode.LIVE)
    assert result == expected_execution
    if execution == BrokerBackend.KITE:
        kite.place_order.assert_awaited_once()
        breeze.place_order.assert_not_awaited()
    else:
        breeze.place_order.assert_awaited_once()
        kite.place_order.assert_not_awaited()


def test_hybrid_market_data_requires_credentials_for_both_routed_brokers():
    with pytest.raises(
        ValueError,
        match="KITE_API_KEY and KITE_API_SECRET are required",
    ):
        PlatformSettings(
            _env_file=None,
            market_data_backend=MarketDataBackend.HYBRID,
            frequent_data_broker=BrokerBackend.KITE,
            reference_data_broker=BrokerBackend.BREEZE,
            breeze_api_key="breeze-key",
            breeze_secret_key="breeze-secret",
        )


@pytest.mark.asyncio
async def test_breeze_and_kite_sessions_can_be_active_concurrently(tmp_path):
    class _Adapter:
        def __init__(self):
            self.is_active = False

        async def authenticate(self, api_key, secret_key, session_token):
            self.is_active = True
            return True

        async def authenticate_access_token(self, api_key, access_token):
            self.is_active = True
            return True

    breeze = _Adapter()
    kite = _Adapter()
    gateway = SimpleNamespace(
        execution_broker_name="breeze",
        adapter_for_broker=lambda broker: kite if str(broker) == "kite" else breeze,
    )
    service = BrokerSessionService(
        repository=BrokerSessionRepository(
            db_path=tmp_path / "broker-session.db",
        ),
        event_bus=Mock(publish=AsyncMock()),
        broker_gateway=gateway,
    )
    await service.initialize()

    kite_result = await service.activate_session(
        api_key="kite-key",
        secret_key="kite-secret",
        session_token="kite-request-token",
        access_token="kite-access-token",
        account_id="ZERODHA_PRIMARY",
        broker_backend="kite",
    )
    breeze_result = await service.activate_session(
        api_key="breeze-key",
        secret_key="breeze-secret",
        session_token="breeze-session-token",
        account_id="ICICI_PRIMARY",
        broker_backend="breeze",
    )

    assert kite_result["connected"] is True
    assert breeze_result["connected"] is True

    statuses = await service.get_all_session_statuses()
    assert statuses["kite"]["connected"] is True
    assert statuses["breeze"]["connected"] is True
    assert service.get_runtime_credentials("kite")["session_token"] == "kite-access-token"
    assert service.get_runtime_credentials("breeze")["session_token"] == "breeze-session-token"


def test_live_execution_broker_legacy_alias_remains_accepted():
    settings = PlatformSettings(
        _env_file=None,
        BROKER_BACKEND="kite",
    )
    assert settings.broker_backend == BrokerBackend.KITE
