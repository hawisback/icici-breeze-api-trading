"""Broker Stream Port Protocol.
"""

from __future__ import annotations

from typing import Callable, Coroutine, Optional, Protocol

from services.broker_gateway.domain.enums import FeedInterval
from services.broker_gateway.domain.models.instrument import BrokerInstrumentRef


class BrokerStreamPort(Protocol):
    """Port for streaming real-time market data and order notifications."""

    async def connect_stream(self) -> None:
        """Initialize and start WebSocket stream."""
        ...

    async def disconnect_stream(self) -> None:
        """Gracefully disconnect WebSocket stream."""
        ...

    async def subscribe_quotes(self, instrument: BrokerInstrumentRef) -> None:
        """Subscribe to real-time quotes."""
        ...

    async def subscribe_candles(self, instrument: BrokerInstrumentRef, interval: FeedInterval) -> None:
        """Subscribe to OHLC candles."""
        ...

    async def subscribe_order_notifications(self) -> None:
        """Subscribe to execution and order update events."""
        ...

