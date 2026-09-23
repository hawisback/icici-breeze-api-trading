"""Execution Service processing approved orders and reconciling broker fills."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from libs.broker_models.adapter import BrokerOrderRequest
from libs.contracts.models import BrokerOrder, OrderState, TradingMode, utc_now
from libs.events.bus import EventBus, EventEnvelope, Topics, get_event_bus
from services.broker_gateway.service import BrokerGatewayService
from services.oms.service import OMSService
from services.risk.live_gate import LiveTradingGate

logger = logging.getLogger(__name__)

_RECONCILE_STATES = {
    OrderState.ACKNOWLEDGED,
    OrderState.OPEN,
    OrderState.PARTIALLY_FILLED,
    OrderState.SUBMISSION_UNKNOWN,
}


class ExecutionService:
    """Routes orders to their owning broker and reconciles status until terminal."""

    def __init__(
        self,
        broker_gateway: BrokerGatewayService,
        oms_service: OMSService,
        live_gate: Optional[LiveTradingGate] = None,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self.gateway = broker_gateway
        self.oms = oms_service
        self.bus = event_bus or get_event_bus()
        self.live_gate = live_gate or LiveTradingGate(event_bus=self.bus)
        self._processed_executions: set[str] = set()
        self._reconciliation_task: Optional[asyncio.Task[None]] = None
        self._reconciliation_running = False

    async def initialize(self) -> None:
        await self.bus.subscribe(Topics.EXECUTION_COMMAND, self._handle_execution_command)

    async def start_reconciliation_worker(self, interval_sec: float = 1.0) -> None:
        if self._reconciliation_running:
            return
        self._reconciliation_running = True
        self._reconciliation_task = asyncio.create_task(
            self._reconciliation_loop(interval_sec)
        )
        logger.info("Execution broker reconciliation worker started.")

    async def stop_reconciliation_worker(self) -> None:
        self._reconciliation_running = False
        if self._reconciliation_task:
            self._reconciliation_task.cancel()
            try:
                await self._reconciliation_task
            except asyncio.CancelledError:
                pass
            self._reconciliation_task = None

    async def _handle_execution_command(self, envelope: EventEnvelope[Any]) -> None:
        payload = envelope.payload
        order_id = payload.get("order_id")
        client_order_id = payload.get("client_order_id")
        if client_order_id in self._processed_executions:
            logger.warning("Duplicate execution command ignored for %s", client_order_id)
            return
        self._processed_executions.add(client_order_id)
        await self.execute_order(order_id=order_id, client_order_id=client_order_id)

    @staticmethod
    def _account_for_broker(broker: Optional[str]) -> str:
        return "ZERODHA_PRIMARY" if str(broker or "").lower() == "kite" else "ICICI_PRIMARY"

    async def execute_order(self, order_id: str, client_order_id: str) -> None:
        order = await self.oms.get_order(order_id)
        if not order:
            logger.error("ExecutionService: Order %s not found in OMS", order_id)
            return

        execution_broker = order.execution_broker or self.gateway.active_broker_name
        if order.trading_mode == TradingMode.LIVE:
            authorized, reason = self.live_gate.validate_live_order(
                account_id=self._account_for_broker(execution_broker)
            )
            if not authorized:
                await self._publish_order_status(
                    order,
                    broker_order_id=None,
                    status="REJECTED",
                    message=f"Execution rejected: {reason}",
                )
                return

        broker_stock_code = (
            order.symbol
            if str(execution_broker).lower() == "kite"
            else (order.stock_code or order.symbol)
        )
        req = BrokerOrderRequest(
            client_order_id=order.client_order_id,
            stock_code=broker_stock_code,
            exchange_code=order.exchange_code or "NFO",
            product="options",
            action=order.side.value.lower(),
            order_type=order.order_type.value.lower(),
            quantity=order.quantity,
            price=order.price,
            expiry_date=order.expiry_date,
            strike_price=order.strike_price,
            right=order.option_right.value.lower() if order.option_right else None,
        )

        try:
            resp = await self.gateway.place_order(
                req,
                mode=order.trading_mode,
                broker=execution_broker,
            )
            await self._publish_order_status(
                order,
                broker_order_id=resp.broker_order_id,
                status=resp.status,
                filled_quantity=resp.filled_quantity,
                average_price=resp.average_price,
                message=resp.message,
            )
        except Exception as exc:
            logger.error(
                "Execution error for order %s: %s. Marking SUBMISSION_UNKNOWN.",
                order.order_id,
                exc,
                exc_info=True,
            )
            await self._publish_order_status(
                order,
                broker_order_id=None,
                status="UNKNOWN",
                message=f"Execution failed: {exc}. Marked for reconciliation.",
            )

    async def _publish_order_status(
        self,
        order: BrokerOrder,
        *,
        broker_order_id: Optional[str],
        status: str,
        filled_quantity: int = 0,
        average_price: float = 0.0,
        message: Optional[str] = None,
    ) -> None:
        payload = {
            "client_order_id": order.client_order_id,
            "broker_order_id": broker_order_id,
            "status": status,
            "filled_quantity": filled_quantity,
            "average_price": average_price,
            "message": message,
            "execution_broker": order.execution_broker or self.gateway.active_broker_name,
        }
        await self.bus.publish(EventEnvelope(topic=Topics.BROKER_ORDER_EVENT, payload=payload))

    async def _reconciliation_loop(self, interval_sec: float) -> None:
        while self._reconciliation_running:
            try:
                await self.reconcile_live_orders()
                await asyncio.sleep(interval_sec)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Execution reconciliation cycle failed")
                await asyncio.sleep(interval_sec)

    async def reconcile_live_orders(self) -> None:
        """Reconcile each non-terminal LIVE order against the broker that owns it."""
        orders = await self.oms.list_orders(limit=200)
        for order in orders:
            if order.trading_mode != TradingMode.LIVE or order.status not in _RECONCILE_STATES:
                continue
            if not order.broker_order_id:
                # Unknown submissions without a broker id require manual/order-book
                # recovery; never blind-resubmit them.
                continue
            broker = order.execution_broker or self.gateway.active_broker_name
            try:
                status = await self.gateway.get_order_status(
                    order.broker_order_id,
                    mode=TradingMode.LIVE,
                    broker=broker,
                )
            except Exception as exc:
                logger.warning(
                    "Order reconciliation deferred broker=%s order=%s: %s",
                    broker,
                    order.broker_order_id,
                    exc,
                )
                continue
            if status is None:
                continue

            normalized = str(status.status or "UNKNOWN").upper()
            changed = (
                normalized != order.status.value
                or int(status.filled_quantity or 0) != order.filled_quantity
                or float(status.average_price or 0.0) != order.average_price
            )
            if changed:
                await self._publish_order_status(
                    order,
                    broker_order_id=status.broker_order_id or order.broker_order_id,
                    status=normalized,
                    filled_quantity=int(status.filled_quantity or 0),
                    average_price=float(status.average_price or 0.0),
                    message=status.message,
                )
