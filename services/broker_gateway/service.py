"""Broker Gateway Service routing orders to appropriate adapter (Paper vs Live Breeze).
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
from libs.contracts.models import TradingMode
from services.broker_gateway.application.services.broker_service import BrokerApplicationService
from services.broker_gateway.icici_breeze_adapter import IciciBreezeAdapter
from services.broker_gateway.paper_adapter import PaperBrokerAdapter

logger = logging.getLogger(__name__)


class BrokerGatewayService:
    """Gateway dispatching orders, queries, and cancellations to the appropriate broker adapter."""

    def __init__(
        self,
        paper_adapter: Optional[PaperBrokerAdapter] = None,
        breeze_adapter: Optional[IciciBreezeAdapter] = None,
    ) -> None:
        self.paper_adapter = paper_adapter or PaperBrokerAdapter()
        self.breeze_adapter = breeze_adapter or IciciBreezeAdapter()

    async def initialize(self) -> None:
        """Initialize underlying adapters and persistent state."""
        if hasattr(self.breeze_adapter, "initialize"):
            await self.breeze_adapter.initialize()

    @property
    def clean_breeze_service(self) -> BrokerApplicationService:
        """Direct access to the Clean Architecture broker service."""
        return self.breeze_adapter.clean_service

    def get_adapter(self, mode: TradingMode) -> BrokerAdapter:
        if mode == TradingMode.LIVE:
            return self.breeze_adapter
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
