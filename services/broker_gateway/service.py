"""Broker Gateway Service separating LIVE execution ownership from data routing.
"""

from __future__ import annotations

import logging
from typing import Optional

from libs.broker_models.adapter import (
    BrokerAdapter,
    BrokerFunds,
    BrokerOrderRequest,
    BrokerOrderResponse,
    BrokerPositionResponse,
    BrokerTradeResponse,
)
from libs.config.settings import BrokerBackend, PlatformSettings, get_settings
from libs.contracts.models import TradingMode
from services.broker_gateway.application.services.broker_service import BrokerApplicationService
from services.broker_gateway.icici_breeze_adapter import IciciBreezeAdapter
from services.broker_gateway.paper_adapter import PaperBrokerAdapter
from services.broker_gateway.zerodha_kite_adapter import ZerodhaKiteAdapter

logger = logging.getLogger(__name__)


class BrokerGatewayService:
    """Gateway dispatching orders, queries, and cancellations to the appropriate broker adapter."""

    def __init__(
        self,
        paper_adapter: Optional[PaperBrokerAdapter] = None,
        breeze_adapter: Optional[IciciBreezeAdapter] = None,
        kite_adapter: Optional[ZerodhaKiteAdapter] = None,
        settings: Optional[PlatformSettings] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.paper_adapter = paper_adapter or PaperBrokerAdapter()
        self.breeze_adapter = breeze_adapter or IciciBreezeAdapter()
        self.kite_adapter = kite_adapter or ZerodhaKiteAdapter(
            api_key=_secret(self.settings.kite_api_key),
            api_secret=_secret(self.settings.kite_api_secret),
            product=self.settings.kite_product,
        )

    async def initialize(self) -> None:
        """Initialize underlying adapters and persistent state."""
        if hasattr(self.breeze_adapter, "initialize"):
            await self.breeze_adapter.initialize()
        if hasattr(self.kite_adapter, "initialize"):
            await self.kite_adapter.initialize()

    @property
    def broker_backend(self) -> BrokerBackend:
        """Compatibility alias for the configured LIVE execution broker."""
        return self.settings.broker_backend

    def adapter_for_broker(self, broker: BrokerBackend | str) -> BrokerAdapter:
        broker_value = (
            broker
            if isinstance(broker, BrokerBackend)
            else BrokerBackend(str(broker).lower())
        )
        return (
            self.kite_adapter
            if broker_value == BrokerBackend.KITE
            else self.breeze_adapter
        )

    @property
    def execution_adapter(self) -> BrokerAdapter:
        """Return the only adapter authorized to own LIVE order state."""
        return self.adapter_for_broker(self.settings.broker_backend)

    @property
    def execution_broker_name(self) -> str:
        return self.settings.broker_backend.value

    @property
    def active_adapter(self) -> BrokerAdapter:
        """Compatibility alias for the LIVE execution adapter."""
        return self.execution_adapter

    @property
    def active_broker_name(self) -> str:
        """Compatibility alias for the LIVE execution broker name."""
        return self.execution_broker_name

    def _configured_data_broker(self, *, frequent: bool) -> BrokerBackend:
        backend = self.settings.market_data_backend
        backend_value = getattr(backend, "value", str(backend)).lower()
        if backend_value == "kite":
            return BrokerBackend.KITE
        if backend_value == "breeze":
            return BrokerBackend.BREEZE
        if backend_value == "hybrid":
            return (
                self.settings.frequent_data_broker
                if frequent
                else self.settings.reference_data_broker
            )
        # Simulated mode has no external data owner; retain execution broker
        # only as an adapter lookup fallback for explicit calls.
        return self.settings.broker_backend

    @property
    def frequent_data_broker(self) -> BrokerBackend:
        return self._configured_data_broker(frequent=True)

    @property
    def reference_data_broker(self) -> BrokerBackend:
        return self._configured_data_broker(frequent=False)

    @property
    def frequent_data_adapter(self) -> BrokerAdapter:
        return self.adapter_for_broker(self.frequent_data_broker)

    @property
    def reference_data_adapter(self) -> BrokerAdapter:
        return self.adapter_for_broker(self.reference_data_broker)

    @property
    def frequent_data_broker_name(self) -> str:
        return self.frequent_data_broker.value

    @property
    def reference_data_broker_name(self) -> str:
        return self.reference_data_broker.value

    def is_broker_active(self, broker: BrokerBackend | str) -> bool:
        adapter = self.adapter_for_broker(broker)
        active = bool(getattr(adapter, "is_active", False))
        if not active and hasattr(adapter, "client_manager"):
            active = bool(getattr(adapter.client_manager, "is_active", False))
        return active

    @property
    def clean_breeze_service(self) -> BrokerApplicationService:
        """Direct access to the Clean Architecture broker service."""
        return self.breeze_adapter.clean_service

    def get_adapter(self, mode: TradingMode) -> BrokerAdapter:
        if mode == TradingMode.LIVE:
            return self.execution_adapter
        # Default and fallback is paper execution
        return self.paper_adapter

    async def get_funds(self, mode: TradingMode = TradingMode.PAPER) -> BrokerFunds:
        return await self.get_adapter(mode).get_funds()

    async def place_order(
        self,
        request: BrokerOrderRequest,
        mode: TradingMode = TradingMode.PAPER,
    ) -> BrokerOrderResponse:
        return await self.get_adapter(mode).place_order(request)

    async def modify_order(
        self,
        broker_order_id: str,
        quantity: Optional[int] = None,
        price: Optional[float] = None,
        mode: TradingMode = TradingMode.PAPER,
    ) -> BrokerOrderResponse:
        return await self.get_adapter(mode).modify_order(broker_order_id, quantity, price)

    async def cancel_order(
        self,
        broker_order_id: str,
        mode: TradingMode = TradingMode.PAPER,
    ) -> BrokerOrderResponse:
        return await self.get_adapter(mode).cancel_order(broker_order_id)

    async def get_order_status(
        self,
        broker_order_id: str,
        mode: TradingMode = TradingMode.PAPER,
    ) -> Optional[BrokerOrderResponse]:
        return await self.get_adapter(mode).get_order_status(broker_order_id)

    async def find_order_by_client_id(
        self,
        client_order_id: str,
        mode: TradingMode = TradingMode.PAPER,
    ) -> Optional[BrokerOrderResponse]:
        return await self.get_adapter(mode).find_order_by_client_id(client_order_id)

    async def get_positions(
        self,
        mode: TradingMode = TradingMode.PAPER,
    ) -> list[BrokerPositionResponse]:
        return await self.get_adapter(mode).get_positions()

    async def get_trades(
        self,
        mode: TradingMode = TradingMode.PAPER,
    ) -> list[BrokerTradeResponse]:
        return await self.get_adapter(mode).get_trades()


def _secret(value: object) -> str:
    return value.get_secret_value() if hasattr(value, "get_secret_value") else str(value or "")
