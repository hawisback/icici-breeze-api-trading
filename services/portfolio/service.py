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

        # Record execution
        execution = Execution(
            execution_id=generate_id(),
            order_id=payload["order_id"],
            broker_execution_id=payload.get("broker_execution_id"),
            instrument_id=inst_id,
            symbol=symbol,
            side=side,
            quantity=qty,
            price=price,
        )
        await self.repo.save_execution(execution)

        # Update position
        pos = await self.repo.get_position(inst_id)
        if not pos:
            pos = Position(
                position_id=generate_id(),
                instrument_id=inst_id,
                symbol=symbol,
                trading_mode=TradingMode(payload.get("trading_mode", "PAPER")),
            )

        new_buy_qty = pos.buy_quantity + (qty if side == OrderSide.BUY else 0)
        new_sell_qty = pos.sell_quantity + (qty if side == OrderSide.SELL else 0)
        new_buy_val = pos.buy_value + (qty * price if side == OrderSide.BUY else 0.0)
        new_sell_val = pos.sell_value + (qty * price if side == OrderSide.SELL else 0.0)
        net_qty = new_buy_qty - new_sell_qty

        # Average price & Realized PnL calculation
        avg_price = (new_buy_val / new_buy_qty) if new_buy_qty > 0 else 0.0
        # If closing partially or fully:
        closed_qty = min(new_buy_qty, new_sell_qty)
        realized_pnl = (new_sell_val / new_sell_qty * closed_qty - new_buy_val / new_buy_qty * closed_qty) if closed_qty > 0 else 0.0

        current_price = price
        if self.mkt_svc:
            q = self.mkt_svc.get_latest_quote(inst_id)
            if q:
                current_price = q.last_price

        unrealized_pnl = (current_price - avg_price) * net_qty if net_qty != 0 else 0.0
        total_pnl = round(realized_pnl + unrealized_pnl, 2)

        updated_pos = pos.model_copy(
            update={
                "quantity": net_qty,
                "buy_quantity": new_buy_qty,
                "sell_quantity": new_sell_qty,
                "buy_value": new_buy_val,
                "sell_value": new_sell_val,
                "average_price": round(avg_price, 2),
                "current_price": round(current_price, 2),
                "realized_pnl": round(realized_pnl, 2),
                "unrealized_pnl": round(unrealized_pnl, 2),
                "total_pnl": total_pnl,
                "updated_at": utc_now(),
            }
        )
        await self.repo.save_position(updated_pos)

        # Publish position event
        await self.bus.publish(
            EventEnvelope(topic=Topics.PORTFOLIO_POSITION, payload=updated_pos.model_dump())
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

