"""Strategy Service managing strategy lifecycles, signal generation, and order intent creation.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from libs.contracts.models import (
    OrderIntent,
    OrderSide,
    OrderType,
    ProductType,
    Signal,
    SourceType,
    TradingMode,
    generate_id,
    utc_now,
)
from libs.events.bus import EventBus, EventEnvelope, Topics, get_event_bus
from services.oms.service import OMSService
from services.strategy.repository import StrategyRepository

logger = logging.getLogger(__name__)


class StrategyService:
    """Calculates algorithmic indicators, detects entry/exit signals, and routes OrderIntents."""

    def __init__(
        self,
        oms_service: OMSService,
        repository: Optional[StrategyRepository] = None,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self.oms = oms_service
        self.repo = repository or StrategyRepository()
        self.bus = event_bus or get_event_bus()

    async def initialize(self) -> None:
        await self.repo.initialize()
        await self._seed_default_strategy()

    async def _seed_default_strategy(self) -> None:
        """Seed default EMA Momentum Options breakout strategy."""
        def_id = "DEF-EMA-OPTIONS-V1"
        await self.repo.save_definition(
            definition_id=def_id,
            name="EMA Breakout Options Strategy",
            version="1.0.0",
            description="5-min EMA 9/21 cross strategy for NIFTY ATM call and put options",
        )
        # Create default paper instance
        inst_id = "INST-NIFTY-EMA-PAPER"
        await self.repo.save_instance(
            instance_id=inst_id,
            definition_id=def_id,
            name="NIFTY EMA Paper Runner",
            mode=TradingMode.PAPER,
            symbol="NIFTY",
            parameters={"fast_period": 9, "slow_period": 21, "lot_multiplier": 1},
            status="RUNNING",
        )

    async def emit_signal(
        self,
        instance_id: str,
        symbol: str,
        instrument_id: str,
        side: OrderSide,
        suggested_price: float,
        quantity: int,
        trading_mode: TradingMode = TradingMode.PAPER,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Signal:
        """Emit a calculated strategy signal and dispatch OrderIntent if active."""
        signal = Signal(
            signal_id=generate_id(),
            strategy_instance_id=instance_id,
            symbol=symbol,
            side=side,
            suggested_price=suggested_price,
            suggested_quantity=quantity,
            confidence=0.92,
            metadata=metadata or {},
        )

        # 1. Save signal to strategy.db
        await self.repo.save_signal(signal)

        # 2. Publish signal to event bus
        await self.bus.publish(
            EventEnvelope(topic=Topics.STRATEGY_SIGNAL, payload=signal.model_dump())
        )

        logger.info(
            "Strategy %s generated signal: %s %s @ %.2f (mode=%s)",
            instance_id,
            side.value,
            symbol,
            suggested_price,
            trading_mode.value,
        )

        # 3. If in SHADOW mode, DO NOT create order intent
        if trading_mode == TradingMode.SHADOW:
            logger.info("SHADOW mode active: Signal logged, no order created.")
            return signal

        # 4. In PAPER or LIVE mode: Submit OrderIntent strictly through OMS -> Risk pipeline
        intent = OrderIntent(
            intent_id=generate_id(),
            correlation_id=generate_id(),
            strategy_instance_id=instance_id,
            source=SourceType.STRATEGY,
            instrument_id=instrument_id,
            symbol=symbol,
            side=side,
            order_type=OrderType.LIMIT,
            quantity=quantity,
            price=suggested_price,
            product=ProductType.OPTIONS,
            trading_mode=trading_mode,
        )

        await self.oms.create_order_intent(intent)
        return signal

    async def list_instances(self) -> list[dict[str, Any]]:
        return await self.repo.list_instances()

