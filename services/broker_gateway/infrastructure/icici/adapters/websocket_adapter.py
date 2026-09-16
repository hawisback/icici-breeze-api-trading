"""Breeze WebSocket Stream Adapter implementing BrokerStreamPort.

Architectural Guarantees:
- Callback on_ticks is strictly non-blocking (puts items directly into an async queue).
- Zero SQLite writes, zero HTTP calls, zero strategy logic inside on_ticks.
- Dedicated health tracking for market and order streams.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Callable, Optional

from services.broker_gateway.domain.enums import FeedInterval
from services.broker_gateway.domain.models.instrument import BrokerInstrumentRef
from services.broker_gateway.domain.ports.stream_port import BrokerStreamPort
from services.broker_gateway.infrastructure.icici.breeze_client import BreezeClientManager
from services.broker_gateway.infrastructure.icici.datetime_mapper import to_breeze_date_str
from services.broker_gateway.infrastructure.icici.request_mapper import (
    map_exchange_to_breeze,
    map_option_right_to_breeze,
    map_product_to_breeze,
)

logger = logging.getLogger(__name__)


class BreezeWebSocketAdapter(BrokerStreamPort):
    """Adapter for streaming real-time ticks and order execution notifications."""

    def __init__(
        self,
        client_manager: BreezeClientManager,
        tick_queue: Optional[asyncio.Queue[dict[str, Any]]] = None,
    ) -> None:
        self.client_manager = client_manager
        self.tick_queue = tick_queue or asyncio.Queue(maxsize=10000)
        self._connected = False
        self._order_notifications_subscribed = False
        self._subscription_count = 0
        self._last_market_event_at: Optional[datetime] = None
        self._last_order_event_at: Optional[datetime] = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def subscription_count(self) -> int:
        return self._subscription_count

    def get_health_stats(self) -> dict[str, Any]:
        return {
            "market_socket_connected": self._connected,
            "order_socket_connected": self._order_notifications_subscribed,
            "subscription_count": self._subscription_count,
            "last_market_event_at": self._last_market_event_at.isoformat() if self._last_market_event_at else None,
            "last_order_event_at": self._last_order_event_at.isoformat() if self._last_order_event_at else None,
        }

    async def connect_stream(self) -> None:
        """Connect WebSocket stream and register non-blocking callback."""
        sdk = self.client_manager.get_sdk_client()

        loop = asyncio.get_running_loop()

        def _on_ticks_callback(ticks: Any) -> None:
            # Strictly non-blocking callback
            now = datetime.now(timezone.utc)
            if isinstance(ticks, dict) and (ticks.get("order_id") or ticks.get("order_status")):
                self._last_order_event_at = now
            else:
                self._last_market_event_at = now

            try:
                loop.call_soon_threadsafe(
                    lambda: self.tick_queue.put_nowait(ticks if isinstance(ticks, dict) else {"data": ticks})
                )
            except Exception as e:
                logger.warning("Failed to enqueue tick into queue: %s", e)

        sdk.on_ticks = _on_ticks_callback

        def _do_connect():
            sdk.ws_connect()

        logger.info("Connecting Breeze WebSocket...")
        await self.client_manager.sdk_runner.run(_do_connect, timeout_sec=15.0)
        self._connected = True
        logger.info("Breeze WebSocket successfully connected.")

    async def disconnect_stream(self) -> None:
        """Disconnect WebSocket stream."""
        if not self._connected:
            return
        sdk = self.client_manager.get_sdk_client()

        def _do_disconnect():
            if hasattr(sdk, "ws_disconnect"):
                sdk.ws_disconnect()

        await self.client_manager.sdk_runner.run(_do_disconnect, timeout_sec=5.0)
        self._connected = False
        self._order_notifications_subscribed = False
        logger.info("Breeze WebSocket disconnected.")

    async def subscribe_quotes(self, instrument: BrokerInstrumentRef) -> None:
        """Subscribe to real-time quotes for an instrument."""
        sdk = self.client_manager.get_sdk_client()
        expiry_str = ""
        if instrument.expiry:
            expiry_str = f"{to_breeze_date_str(instrument.expiry)}T06:00:00.000Z"
        strike_str = str(instrument.strike) if instrument.strike is not None else "0"

        def _sub():
            sdk.subscribe_feeds(
                stock_code=instrument.stock_code,
                exchange_code=map_exchange_to_breeze(instrument.exchange),
                product_type=map_product_to_breeze(instrument.product_type),
                expiry_date=expiry_str,
                strike_price=strike_str,
                right=map_option_right_to_breeze(instrument.option_right),
            )

        await self.client_manager.sdk_runner.run(_sub, timeout_sec=8.0)
        self._subscription_count += 1
        logger.info("Subscribed to Breeze quote feed for %s", instrument.stock_code)

    async def subscribe_candles(self, instrument: BrokerInstrumentRef, interval: FeedInterval) -> None:
        """Subscribe to OHLC candlestick feeds."""
        sdk = self.client_manager.get_sdk_client()
        expiry_str = ""
        if instrument.expiry:
            expiry_str = f"{to_breeze_date_str(instrument.expiry)}T06:00:00.000Z"
        strike_str = str(instrument.strike) if instrument.strike is not None else "0"

        def _sub():
            sdk.subscribe_feeds(
                stock_code=instrument.stock_code,
                exchange_code=map_exchange_to_breeze(instrument.exchange),
                product_type=map_product_to_breeze(instrument.product_type),
                expiry_date=expiry_str,
                strike_price=strike_str,
                right=map_option_right_to_breeze(instrument.option_right),
                interval=interval.value,
            )

        await self.client_manager.sdk_runner.run(_sub, timeout_sec=8.0)
        self._subscription_count += 1
        logger.info("Subscribed to Breeze OHLC feed for %s at interval %s", instrument.stock_code, interval.value)

    async def subscribe_order_notifications(self) -> None:
        """Subscribe to execution and order update notifications."""
        sdk = self.client_manager.get_sdk_client()

        def _sub():
            sdk.subscribe_feeds(get_order_notification=True)

        await self.client_manager.sdk_runner.run(_sub, timeout_sec=8.0)
        self._order_notifications_subscribed = True
        logger.info("Subscribed to Breeze order notifications feed.")

