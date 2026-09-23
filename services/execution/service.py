"""Execution Service processing approved orders, enforcing idempotency and no-blind-retries.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from libs.broker_models.adapter import BrokerOrderRequest, BrokerOrderResponse
from libs.contracts.models import BrokerOrder, OrderState, TradingMode, generate_id, utc_now
from libs.events.bus import EventBus, EventEnvelope, Topics, get_event_bus
from services.broker_gateway.service import BrokerGatewayService
from services.oms.service import OMSService
from services.risk.live_gate import LiveTradingGate

logger = logging.getLogger(__name__)


class ExecutionService:
    """Consumes approved order execution commands and routes them through the Broker Gateway."""

    def __init__(
        self,
        broker_gateway: BrokerGatewayService,
        oms_service: OMSService,
        live_gate: Optional[LiveTradingGate] = None,
        event_bus: Optional[EventBus] = None,
        live_account_id: str = "ICICI_PRIMARY",
    ) -> None:
        self.gateway = broker_gateway
        self.oms = oms_service
        self.bus = event_bus or get_event_bus()
        self.live_gate = live_gate or LiveTradingGate(event_bus=self.bus)
        self.live_account_id = live_account_id
        self._processed_executions: set[str] = set()

    async def initialize(self) -> None:
        await self.bus.subscribe(Topics.EXECUTION_COMMAND, self._handle_execution_command)

    async def _handle_execution_command(self, envelope: EventEnvelope[Any]) -> None:
        payload = envelope.payload
        order_id = payload.get("order_id")
        client_order_id = payload.get("client_order_id")

        if client_order_id in self._processed_executions:
            logger.warning("Duplicate execution command ignored for %s", client_order_id)
            return
        self._processed_executions.add(client_order_id)

        await self.execute_order(order_id=order_id, client_order_id=client_order_id)

    async def execute_order(self, order_id: str, client_order_id: str) -> None:
        """Fetch order from OMS and place with Broker Gateway."""
        order = await self.oms.get_order(order_id)
        if not order:
            logger.error("ExecutionService: Order %s not found in OMS", order_id)
            return

        # Fail-closed LIVE check at the final execution boundary. Reduce-only
        # exits intentionally remain executable after authorization revocation.
        if order.trading_mode == TradingMode.LIVE and not order.reduce_only:
            authorized, reason = self.live_gate.validate_live_order(account_id=self.live_account_id)
            if not authorized:
                logger.error("Execution blocked: LIVE trading unauthorized (%s)", reason)
                await self.bus.publish(
                    EventEnvelope(
                        topic=Topics.BROKER_ORDER_EVENT,
                        payload={
                            "client_order_id": order.client_order_id,
                            "broker_order_id": None,
                            "status": "REJECTED",
                            "message": f"Execution rejected: {reason}",
                        },
                    )
                )
                return

        # Prepare normalized broker order request
        req = BrokerOrderRequest(
            client_order_id=order.client_order_id,
            stock_code=order.symbol,
            exchange_code="NFO",
            action=order.side.value.lower(),
            order_type=order.order_type.value.lower(),
            quantity=order.quantity,
            price=order.price,
        )

        try:
            # Place order via Broker Gateway
            resp = await self.gateway.place_order(req, mode=order.trading_mode)

            # Publish result to broker.order.event.v1
            await self.bus.publish(
                EventEnvelope(
                    topic=Topics.BROKER_ORDER_EVENT,
                    payload={
                        "client_order_id": resp.client_order_id,
                        "broker_order_id": resp.broker_order_id,
                        "status": resp.status,
                        "filled_quantity": resp.filled_quantity,
                        "average_price": resp.average_price,
                        "message": resp.message,
                    },
                )
            )

            # If filled, also emit broker.trade.event.v1 for Portfolio service
            if resp.status == "FILLED":
                await self.bus.publish(
                    EventEnvelope(
                        topic=Topics.BROKER_TRADE_EVENT,
                        payload={
                            "order_id": order.order_id,
                            "client_order_id": order.client_order_id,
                            "instrument_id": order.instrument_id,
                            "symbol": order.symbol,
                            "side": order.side.value,
                            "quantity": resp.filled_quantity,
                            "price": resp.average_price,
                            "trading_mode": order.trading_mode.value,
                            "execution_time": utc_now().isoformat(),
                        },
                    )
                )

        except Exception as e:
            logger.error(
                "Execution error for order %s: %s. Transitioning to SUBMISSION_UNKNOWN (NEVER blind retry).",
                order.order_id,
                e,
                exc_info=True,
            )
            # Mandatory rule: On unknown failure/timeout, mark SUBMISSION_UNKNOWN
            await self.bus.publish(
                EventEnvelope(
                    topic=Topics.BROKER_ORDER_EVENT,
                    payload={
                        "client_order_id": order.client_order_id,
                        "broker_order_id": None,
                        "status": "UNKNOWN",
                        "message": f"Execution failed: {e}. Marked for reconciliation.",
                    },
                )
            )

