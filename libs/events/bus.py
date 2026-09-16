"""Event definitions, schemas (v1), EventBus protocol, and in-memory event bus.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from typing import Any, Generic, Optional, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from libs.contracts.models import generate_id, utc_now

logger = logging.getLogger(__name__)

T = TypeVar("T")


class Topics:
    """Canonical event topics."""
    MARKET_QUOTE = "market.quote.v1"
    MARKET_CANDLE = "market.candle.v1"
    STRATEGY_SIGNAL = "strategy.signal.v1"
    ORDER_INTENT = "order.intent.v1"
    RISK_DECISION = "risk.decision.v1"
    EXECUTION_COMMAND = "execution.command.v1"
    EXECUTION_RESULT = "execution.result.v1"
    BROKER_ORDER_EVENT = "broker.order.event.v1"
    BROKER_TRADE_EVENT = "broker.trade.event.v1"
    ORDER_STATE = "order.state.v1"
    PORTFOLIO_POSITION = "portfolio.position.v1"
    PORTFOLIO_PNL = "portfolio.pnl.v1"
    SYSTEM_STATE = "system.state.v1"
    AUDIT_EVENT = "audit.event.v1"
    NOTIFICATION_EVENT = "notification.event.v1"


class EventEnvelope(BaseModel, Generic[T]):
    """Standard envelope wrapping all domain events."""
    event_id: str = Field(default_factory=generate_id)
    topic: str
    correlation_id: str = Field(default_factory=generate_id)
    occurred_at: datetime = Field(default_factory=utc_now)
    payload: T

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
    )


EventHandler = Callable[[EventEnvelope[Any]], Awaitable[None]]


class EventBus(Protocol):
    """Protocol for event-bus publishers and subscribers."""

    async def publish(self, envelope: EventEnvelope[Any]) -> None:
        """Publish an event envelope to its assigned topic."""
        ...

    async def subscribe(self, topic: str, handler: EventHandler) -> None:
        """Register an async handler for a given topic."""
        ...

    async def start(self) -> None:
        """Start the event bus dispatcher."""
        ...

    async def stop(self) -> None:
        """Stop the event bus and drain queues."""
        ...


class InMemoryEventBus:
    """High-performance async in-memory event bus with topic-based routing."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = defaultdict(list)
        self._queue: asyncio.Queue[EventEnvelope[Any]] = asyncio.Queue()
        self._running: bool = False
        self._worker_task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._queue = asyncio.Queue()
        self._running = True
        self._worker_task = asyncio.create_task(self._dispatcher_loop())
        logger.info("InMemoryEventBus started")

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        logger.info("InMemoryEventBus stopped")

    async def publish(self, envelope: EventEnvelope[Any]) -> None:
        """Queue event for dispatch."""
        await self._queue.put(envelope)

    async def subscribe(self, topic: str, handler: EventHandler) -> None:
        self._subscribers[topic].append(handler)
        logger.debug("Subscribed %s to topic %s", handler, topic)

    async def _dispatcher_loop(self) -> None:
        while self._running:
            try:
                envelope = await self._queue.get()
                handlers = self._subscribers.get(envelope.topic, [])
                for handler in handlers:
                    try:
                        await handler(envelope)
                    except Exception as e:
                        logger.error(
                            "Error in event handler %s for topic %s: %s",
                            handler,
                            envelope.topic,
                            e,
                            exc_info=True,
                        )
                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in event bus dispatcher: %s", e, exc_info=True)


# Global singleton instance for standalone / in-process execution
_default_bus: Optional[InMemoryEventBus] = None


def get_event_bus() -> InMemoryEventBus:
    """Return default application event bus."""
    global _default_bus
    if _default_bus is None:
        _default_bus = InMemoryEventBus()
    return _default_bus
