"""Broker Trading Port Protocol.
"""

from __future__ import annotations

from typing import Optional, Protocol

from services.broker_gateway.domain.models.orders import (
    BrokerOrderAcknowledgement,
    BrokerOrderDetail,
    BrokerOrderRequest,
    CancelBrokerOrderRequest,
    ModifyBrokerOrderRequest,
    SquareOffRequest,
)
from services.broker_gateway.domain.models.positions import BrokerPositionDetail
from services.broker_gateway.domain.models.trades import BrokerTradeDetail


class BrokerTradingPort(Protocol):
    """Port for order placement, modification, cancellation, and book reconciliation."""

    async def place_order(self, request: BrokerOrderRequest) -> BrokerOrderAcknowledgement:
        """Submit a new order to the broker."""
        ...

    async def modify_order(self, request: ModifyBrokerOrderRequest) -> BrokerOrderAcknowledgement:
        """Modify an existing working order."""
        ...

    async def cancel_order(self, request: CancelBrokerOrderRequest) -> BrokerOrderAcknowledgement:
        """Cancel an open order."""
        ...

    async def square_off(self, request: SquareOffRequest) -> BrokerOrderAcknowledgement:
        """Square off an existing open position."""
        ...

    async def get_orders(self) -> list[BrokerOrderDetail]:
        """Fetch all orders for the current trading day."""
        ...

    async def get_order_detail(self, broker_order_id: str) -> Optional[BrokerOrderDetail]:
        """Fetch specific order details."""
        ...

    async def get_trades(self) -> list[BrokerTradeDetail]:
        """Fetch executed trades for the day."""
        ...

    async def get_positions(self) -> list[BrokerPositionDetail]:
        """Fetch open and closed portfolio positions."""
        ...

