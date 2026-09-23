"""Order Management System service orchestrating order lifecycle and transactional outbox.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from libs.contracts.models import (
    BrokerOrder,
    OrderIntent,
    OrderState,
    RiskDecision,
    generate_id,
    utc_now,
)
from libs.events.bus import EventBus, EventEnvelope, Topics, get_event_bus
from services.oms.repository import OMSRepository
from services.oms.state_machine import OrderStateMachine

logger = logging.getLogger(__name__)


class OMSService:
    """Manages order creation, state machine transitions, and reliable outbox publishing."""

    def __init__(
        self,
        repository: Optional[OMSRepository] = None,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self.repo = repository or OMSRepository()
        self.bus = event_bus or get_event_bus()
        self._outbox_worker_task: Optional[asyncio.Task[None]] = None
        self._running: bool = False

    async def initialize(self) -> None:
        await self.repo.initialize()
        # Subscribe to risk decisions and broker updates
        await self.bus.subscribe(Topics.RISK_DECISION, self._handle_risk_decision_event)
        await self.bus.subscribe(Topics.BROKER_ORDER_EVENT, self._handle_broker_order_event)

    async def start_outbox_worker(self, poll_interval_sec: float = 0.2) -> None:
        """Start background loop publishing local outbox events to the event bus."""
        if self._running:
            return
        self._running = True
        self._outbox_worker_task = asyncio.create_task(self._outbox_loop(poll_interval_sec))
        logger.info("OMS outbox publisher worker started.")

    async def stop_outbox_worker(self) -> None:
        self._running = False
        if self._outbox_worker_task:
            self._outbox_worker_task.cancel()
            try:
                await self._outbox_worker_task
            except asyncio.CancelledError:
                pass
            self._outbox_worker_task = None
        logger.info("OMS outbox publisher worker stopped.")

    async def create_order_intent(self, intent: OrderIntent) -> BrokerOrder:
        """Entry point for manual or strategy orders: Persists intent and initial order state."""
        client_order_id = f"CL-{generate_id().replace('-', '')[-12:].upper()}"
        order_id = generate_id()
        now = utc_now()

        order = BrokerOrder(
            order_id=order_id,
            intent_id=intent.intent_id,
            client_order_id=client_order_id,
            instrument_id=intent.instrument_id,
            symbol=intent.symbol,
            side=intent.side,
            order_type=intent.order_type,
            quantity=intent.quantity,
            remaining_quantity=intent.quantity,
            price=intent.price,
            trigger_price=intent.trigger_price,
            status=OrderState.CREATED,
            trading_mode=intent.trading_mode,
            reduce_only=intent.reduce_only,
            created_at=now,
            updated_at=now,
        )

        # 1. Atomically save intent & queue outbox
        await self.repo.save_order_intent(intent, outbox_topic=Topics.ORDER_INTENT)

        # 2. Save initial order in CREATED state
        await self.repo.save_broker_order_with_transition(
            order=order,
            from_state=None,
            to_state=OrderState.CREATED,
            reason="Order intent created",
            outbox_topic=Topics.ORDER_STATE,
        )

        # 3. Transition to VALIDATING (awaiting Risk decision)
        OrderStateMachine.validate_transition(OrderState.CREATED, OrderState.VALIDATING)
        order = order.model_copy(update={"status": OrderState.VALIDATING})
        await self.repo.save_broker_order_with_transition(
            order=order,
            from_state=OrderState.CREATED,
            to_state=OrderState.VALIDATING,
            reason="Dispatched to Risk Service for evaluation",
            outbox_topic=Topics.ORDER_STATE,
        )

        # Also immediately notify EventBus for fast-path local dispatch
        await self.bus.publish(
            EventEnvelope(
                topic=Topics.ORDER_INTENT,
                correlation_id=intent.correlation_id,
                payload=intent.model_dump(),
            )
        )

        logger.info(
            "Created order %s (client_order_id=%s) for intent %s",
            order_id,
            client_order_id,
            intent.intent_id,
        )
        return order

    async def _handle_risk_decision_event(self, envelope: EventEnvelope[Any]) -> None:
        """Handle decision emitted by Risk Service."""
        payload = envelope.payload
        intent_id = payload["intent_id"]
        approved = payload["approved"]
        reason = payload.get("reason", "")

        # Find order by intent_id
        orders = await self.repo.list_orders(limit=50)
        matching = [o for o in orders if o.intent_id == intent_id]
        if not matching:
            return
        order = matching[0]

        # Risk decisions may be replayed by the durable outbox. Once the order
        # has advanced beyond VALIDATING, the persisted decision is idempotent.
        if order.status != OrderState.VALIDATING:
            return

        from_state = order.status
        to_state = OrderState.APPROVED if approved else OrderState.RISK_REJECTED
        OrderStateMachine.validate_transition(from_state, to_state, reason)

        updated_order = order.model_copy(
            update={"status": to_state, "status_message": reason}
        )
        await self.repo.save_broker_order_with_transition(
            order=updated_order,
            from_state=from_state,
            to_state=to_state,
            reason=reason,
            outbox_topic=Topics.ORDER_STATE,
        )

        if approved:
            # Emit execution command
            await self.bus.publish(
                EventEnvelope(
                    topic=Topics.EXECUTION_COMMAND,
                    correlation_id=envelope.correlation_id,
                    payload={"order_id": order.order_id, "client_order_id": order.client_order_id},
                )
            )

    async def _handle_broker_order_event(self, envelope: EventEnvelope[Any]) -> None:
        """Handle status update coming from Broker Gateway / Execution Service."""
        payload = envelope.payload
        client_order_id = payload["client_order_id"]
        broker_order_id = payload.get("broker_order_id")
        broker_status = payload.get("status", "").upper()
        filled_qty = payload.get("filled_quantity", 0)
        avg_price = payload.get("average_price", 0.0)

        order = await self.repo.get_order_by_client_id(client_order_id)
        if not order:
            return

        to_state = order.status
        if broker_status == "FILLED":
            to_state = OrderState.FILLED
        elif broker_status in {"PARTIAL", "PARTIALLY_FILLED", "PARTIALLY FILLED"}:
            to_state = OrderState.PARTIALLY_FILLED
        elif broker_status in {"OPEN", "PLACED"}:
            to_state = OrderState.PARTIALLY_FILLED if filled_qty > 0 else OrderState.OPEN
        elif broker_status == "CANCELLED":
            to_state = OrderState.CANCELLED
        elif broker_status == "REJECTED":
            to_state = OrderState.REJECTED
        elif broker_status == "EXPIRED":
            to_state = OrderState.EXPIRED
        elif broker_status == "FAILED_SAFE":
            to_state = OrderState.FAILED_SAFE
        elif broker_status == "UNKNOWN":
            to_state = OrderState.SUBMISSION_UNKNOWN

        state_changed = to_state != order.status
        fill_changed = (
            int(filled_qty or 0) != order.filled_quantity
            or float(avg_price or 0.0) != float(order.average_price or 0.0)
            or (broker_order_id and broker_order_id != order.broker_order_id)
        )
        if state_changed or fill_changed:
            if state_changed:
                OrderStateMachine.validate_transition(order.status, to_state, broker_status)
            rem_qty = max(0, order.quantity - int(filled_qty or 0))
            updated = order.model_copy(
                update={
                    "broker_order_id": broker_order_id or order.broker_order_id,
                    "status": to_state,
                    "filled_quantity": int(filled_qty or 0),
                    "remaining_quantity": rem_qty,
                    "average_price": float(avg_price or order.average_price),
                    "status_message": payload.get("message"),
                }
            )
            await self.repo.save_broker_order_with_transition(
                order=updated,
                from_state=order.status,
                to_state=to_state,
                reason=f"Broker reconciliation: {broker_status}",
                outbox_topic=Topics.ORDER_STATE,
            )

    async def _outbox_loop(self, poll_interval_sec: float) -> None:
        """Poll outbox and publish to event bus."""
        while self._running:
            try:
                events = await self.repo.get_outbox_events_to_publish(limit=20)
                for ev in events:
                    envelope = EventEnvelope(
                        event_id=ev["event_id"],
                        topic=ev["topic"],
                        payload=ev["payload"],
                    )
                    await self.bus.publish(envelope)
                    await self.repo.mark_outbox_published(ev["event_id"])
                await asyncio.sleep(poll_interval_sec)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in OMS outbox dispatcher: %s", e)
                await asyncio.sleep(poll_interval_sec)

    async def mark_order_submitting(self, order_id: str) -> Optional[BrokerOrder]:
        """Durably reserve an approved order for one broker submission."""
        order = await self.repo.get_order_by_id(order_id)
        if order is None:
            return None
        if order.status == OrderState.SUBMITTING:
            return order
        if order.status != OrderState.APPROVED:
            return order
        OrderStateMachine.validate_transition(order.status, OrderState.SUBMITTING, "Execution reserved")
        updated = order.model_copy(
            update={"status": OrderState.SUBMITTING, "updated_at": utc_now()}
        )
        await self.repo.save_broker_order_with_transition(
            order=updated,
            from_state=order.status,
            to_state=OrderState.SUBMITTING,
            reason="Execution service reserved broker submission",
            outbox_topic=Topics.ORDER_STATE,
        )
        return updated

    async def get_order(self, order_id: str) -> Optional[BrokerOrder]:
        return await self.repo.get_order_by_id(order_id)

    async def list_orders(self, limit: int = 100) -> list[BrokerOrder]:
        return await self.repo.list_orders(limit=limit)

