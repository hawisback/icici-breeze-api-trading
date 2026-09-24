"""Portfolio Service tracking trade fills, calculating VWAP positions, and managing live P&L.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from libs.contracts.models import (
    Execution,
    OrderSide,
    PnLSnapshot,
    Position,
    TradingMode,
    generate_id,
    utc_now,
)
from libs.events.bus import EventBus, EventEnvelope, Topics, get_event_bus
from services.market_data.service import MarketDataService
from services.portfolio.repository import PortfolioRepository

logger = logging.getLogger(__name__)


class PortfolioService:
    """Consumes executions, tracks positions, computes real-time PnL, and takes periodic snapshots."""

    def __init__(
        self,
        repository: Optional[PortfolioRepository] = None,
        market_data_service: Optional[MarketDataService] = None,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self.repo = repository or PortfolioRepository()
        self.mkt_svc = market_data_service
        self.bus = event_bus or get_event_bus()

    async def initialize(self) -> None:
        await self.repo.initialize()
        await self.bus.subscribe(Topics.BROKER_TRADE_EVENT, self._handle_trade_event)
        await self.bus.subscribe(Topics.MARKET_QUOTE, self._handle_market_quote)

    async def _handle_trade_event(self, envelope: EventEnvelope[Any]) -> None:
        payload = envelope.payload
        side = OrderSide(payload["side"])
        qty = int(payload["quantity"])
        price = float(payload["price"])
        inst_id = payload["instrument_id"]
        symbol = payload["symbol"]

        execution_id = str(
            payload.get("execution_id")
            or payload.get("broker_execution_id")
            or generate_id()
        )
        execution = Execution(
            execution_id=execution_id,
            order_id=payload["order_id"],
            broker_execution_id=payload.get("broker_execution_id"),
            instrument_id=inst_id,
            symbol=symbol,
            side=side,
            quantity=qty,
            price=price,
        )

        current_price = price
        if self.mkt_svc:
            q = self.mkt_svc.get_latest_quote(inst_id)
            if q:
                current_price = q.last_price

        updated_pos, applied = await self.repo.apply_execution_atomic(
            execution,
            trading_mode=TradingMode(
                payload.get("trading_mode", "PAPER")
            ),
            current_price=current_price,
        )
        if not applied:
            logger.info(
                "Duplicate execution %s ignored during portfolio replay",
                execution_id,
            )
            return

        # Publish position event only after the execution and position were
        # committed atomically.
        await self.bus.publish(
            EventEnvelope(
                topic=Topics.PORTFOLIO_POSITION,
                payload=updated_pos.model_dump(),
            )
        )

        # Recalculate portfolio PnL summary
        await self.publish_pnl_snapshot()

    async def _handle_market_quote(self, envelope: EventEnvelope[Any]) -> None:
        """Update unrealized PnL when market ticks arrive for held positions."""
        payload = envelope.payload
        inst_id = payload["instrument_id"]
        pos = await self.repo.get_position(inst_id)
        if not pos or pos.quantity == 0:
            return

        ltp = float(payload["last_price"])
        unrealized = (ltp - pos.average_price) * pos.quantity
        updated_pos = pos.model_copy(
            update={
                "current_price": ltp,
                "unrealized_pnl": round(unrealized, 2),
                "total_pnl": round(pos.realized_pnl + unrealized, 2),
                "updated_at": utc_now(),
            }
        )
        await self.repo.save_position(updated_pos)

    async def publish_pnl_snapshot(self) -> PnLSnapshot:
        positions = await self.repo.list_positions()
        realized = sum(p.realized_pnl for p in positions)
        unrealized = sum(p.unrealized_pnl for p in positions if p.quantity != 0)
        total = round(realized + unrealized, 2)
        open_count = len([p for p in positions if p.quantity != 0])

        snapshot = PnLSnapshot(
            realized_pnl=round(realized, 2),
            unrealized_pnl=round(unrealized, 2),
            total_pnl=total,
            day_pnl=total,
            open_positions_count=open_count,
            timestamp=utc_now(),
        )
        await self.repo.save_pnl_snapshot(snapshot)
        await self.bus.publish(
            EventEnvelope(topic=Topics.PORTFOLIO_PNL, payload=snapshot.model_dump())
        )
        return snapshot

    async def get_positions(self) -> list[Position]:
        return await self.repo.list_positions()

    async def get_pnl_summary(self) -> dict[str, Any]:
        positions = await self.repo.list_positions()
        realized = sum(p.realized_pnl for p in positions)
        unrealized = sum(p.unrealized_pnl for p in positions if p.quantity != 0)
        total = round(realized + unrealized, 2)
        open_count = len([p for p in positions if p.quantity != 0])

        return {
            "realized_pnl": round(realized, 2),
            "unrealized_pnl": round(unrealized, 2),
            "total_pnl": total,
            "day_pnl": total,
            "open_positions_count": open_count,
            "timestamp": utc_now().isoformat(),
        }

