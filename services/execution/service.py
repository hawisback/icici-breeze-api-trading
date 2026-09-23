"""Execution Service processing approved orders, enforcing idempotency and no-blind-retries.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional

from libs.broker_models.adapter import BrokerOrderRequest, BrokerOrderResponse
from libs.contracts.models import OrderState, TradingMode, utc_now
from libs.events.bus import EventBus, EventEnvelope, Topics, get_event_bus
from services.broker_gateway.service import BrokerGatewayService
from services.oms.service import OMSService
from services.risk.live_gate import LiveTradingGate

logger = logging.getLogger(__name__)


class ExecutionService:
    """Consumes approved orders and reconciles non-terminal broker state."""

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
        self._reconciliation_task: Optional[asyncio.Task[None]] = None
        self._reconciliation_running = False

    async def initialize(self) -> None:
        await self.bus.subscribe(Topics.EXECUTION_COMMAND, self._handle_execution_command)
        if not self._reconciliation_task or self._reconciliation_task.done():
            self._reconciliation_running = True
            self._reconciliation_task = asyncio.create_task(self._reconciliation_loop())
        await self._recover_approved_orders()

    async def _recover_approved_orders(self) -> None:
        """Resume persisted APPROVED orders after a process restart."""
        for order in await self.oms.list_orders(limit=500):
            if order.status != OrderState.APPROVED:
                continue
            await self.execute_order(
                order_id=order.order_id,
                client_order_id=order.client_order_id,
            )

    async def stop(self) -> None:
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

    def _build_broker_request(self, order: Any) -> Optional[BrokerOrderRequest]:
        stock_code = order.symbol
        strike_price = None
        right = None
        expiry_date = None

        if (
            order.trading_mode == TradingMode.LIVE
            and str(getattr(self.gateway, "active_broker_name", "")).lower()
            == "breeze"
        ):
            match = re.fullmatch(
                r"INST-(NIFTY|BANKNIFTY)-(\d{4}-\d{2}-\d{2})-"
                r"(\d+(?:\.\d+)?)-(CE|PE)",
                str(order.instrument_id).upper(),
            )
            if match is None:
                return None
            stock_code = match.group(1)
            expiry_date = match.group(2)
            strike_price = float(match.group(3))
            right = "call" if match.group(4) == "CE" else "put"

        return BrokerOrderRequest(
            client_order_id=order.client_order_id,
            stock_code=stock_code,
            exchange_code="NFO",
            action=order.side.value.lower(),
            order_type=order.order_type.value.lower(),
            quantity=order.quantity,
            price=order.price,
            trigger_price=order.trigger_price,
            strike_price=strike_price,
            right=right,
            expiry_date=expiry_date,
            user_remark=order.client_order_id,
        )

    @staticmethod
    def _normalize_broker_status(status: str) -> str:
        normalized = str(status or "UNKNOWN").upper().replace("-", "_")
        if normalized in {"COMPLETE", "COMPLETED"}:
            return "FILLED"
        if normalized in {"PARTIAL", "PARTIALLY FILLED"}:
            return "PARTIALLY_FILLED"
        return normalized

    async def execute_order(self, order_id: str, client_order_id: str) -> None:
        """Reserve exactly one submission, then route it to the broker."""
        order = await self.oms.get_order(order_id)
        if not order:
            logger.error("ExecutionService: Order %s not found in OMS", order_id)
            return
        if order.status != OrderState.APPROVED:
            logger.warning(
                "ExecutionService: refusing duplicate/non-approved submission for %s in state %s",
                order_id,
                order.status.value,
            )
            return

        # LIVE authorization gates exposure increases. Explicit reduce-only
        # exits remain available after gate expiry/revocation.
        if order.trading_mode == TradingMode.LIVE and not order.reduce_only:
            authorized, reason = self.live_gate.validate_live_order(
                account_id=self.live_account_id
            )
            if not authorized:
                logger.error("Execution blocked: LIVE trading unauthorized (%s)", reason)
                await self.bus.publish(
                    EventEnvelope(
                        topic=Topics.BROKER_ORDER_EVENT,
                        payload={
                            "client_order_id": order.client_order_id,
                            "broker_order_id": None,
                            "status": "FAILED_SAFE",
                            "filled_quantity": order.filled_quantity,
                            "average_price": order.average_price,
                            "message": f"Execution blocked at final LIVE boundary: {reason}",
                        },
                    )
                )
                return

        # Persist APPROVED -> SUBMITTING before the outbound call. A crash after
        # this point is reconciled; the command is never blindly resubmitted.
        order = await self.oms.mark_order_submitting(order_id)
        if not order or order.status != OrderState.SUBMITTING:
            logger.warning("ExecutionService: could not reserve order %s for submission", order_id)
            return

        req = self._build_broker_request(order)
        if req is None:
            await self.bus.publish(
                EventEnvelope(
                    topic=Topics.BROKER_ORDER_EVENT,
                    payload={
                        "client_order_id": order.client_order_id,
                        "broker_order_id": None,
                        "status": "FAILED_SAFE",
                        "filled_quantity": order.filled_quantity,
                        "average_price": order.average_price,
                        "message": (
                            "Breeze option contract identity could not be "
                            "resolved from the canonical instrument id"
                        ),
                    },
                )
            )
            return

        try:
            resp = await self.gateway.place_order(req, mode=order.trading_mode)
            normalized_status = self._normalize_broker_status(resp.status)
            await self.bus.publish(
                EventEnvelope(
                    topic=Topics.BROKER_ORDER_EVENT,
                    payload={
                        "client_order_id": order.client_order_id,
                        "broker_order_id": resp.broker_order_id,
                        "status": normalized_status,
                        "filled_quantity": resp.filled_quantity,
                        "average_price": resp.average_price,
                        "message": resp.message,
                    },
                )
            )

            if normalized_status == "FILLED" and resp.filled_quantity > 0:
                await self._publish_trade_fill(
                    order=order,
                    quantity=resp.filled_quantity,
                    price=resp.average_price or order.price,
                )
        except Exception as exc:
            logger.error(
                "Execution error for order %s: %s. Transitioning to SUBMISSION_UNKNOWN (NEVER blind retry).",
                order.order_id,
                exc,
                exc_info=True,
            )
            await self.bus.publish(
                EventEnvelope(
                    topic=Topics.BROKER_ORDER_EVENT,
                    payload={
                        "client_order_id": order.client_order_id,
                        "broker_order_id": None,
                        "status": "UNKNOWN",
                        "filled_quantity": order.filled_quantity,
                        "average_price": order.average_price,
                        "message": f"Execution failed: {exc}. Marked for reconciliation.",
                    },
                )
            )

    async def _publish_trade_fill(self, *, order: Any, quantity: int, price: float) -> None:
        await self.bus.publish(
            EventEnvelope(
                topic=Topics.BROKER_TRADE_EVENT,
                payload={
                    "order_id": order.order_id,
                    "client_order_id": order.client_order_id,
                    "instrument_id": order.instrument_id,
                    "symbol": order.symbol,
                    "side": order.side.value,
                    "quantity": quantity,
                    "price": price,
                    "trading_mode": order.trading_mode.value,
                    "execution_time": utc_now().isoformat(),
                },
            )
        )

    async def reconcile_live_orders(self) -> None:
        """Reconcile non-terminal LIVE orders against broker evidence."""
        pending_states = {
            OrderState.SUBMITTING,
            OrderState.SUBMISSION_UNKNOWN,
            OrderState.ACKNOWLEDGED,
            OrderState.OPEN,
            OrderState.PARTIALLY_FILLED,
        }
        orders = [
            order
            for order in await self.oms.list_orders(limit=500)
            if order.trading_mode == TradingMode.LIVE and order.status in pending_states
        ]
        if not orders:
            return

        broker_trades = None
        for order in orders:
            try:
                response: Optional[BrokerOrderResponse] = None
                if order.broker_order_id:
                    response = await self.gateway.get_order_status(
                        order.broker_order_id,
                        mode=TradingMode.LIVE,
                    )
                elif order.status in {
                    OrderState.SUBMITTING,
                    OrderState.SUBMISSION_UNKNOWN,
                }:
                    # First recover the broker acknowledgement using the exact
                    # immutable client reference/tag. This closes the crash
                    # window after broker acceptance but before local receipt.
                    response = await self.gateway.find_order_by_client_id(
                        order.client_order_id,
                        mode=TradingMode.LIVE,
                    )
                    if response is None:
                        # Trade evidence is a secondary recovery path. Absence
                        # of evidence never triggers a retry.
                        if broker_trades is None:
                            broker_trades = await self.gateway.get_trades(
                                mode=TradingMode.LIVE
                            )
                        matches = [
                            trade
                            for trade in broker_trades
                            if trade.client_order_id == order.client_order_id
                        ]
                        if matches:
                            filled = sum(max(0, trade.quantity) for trade in matches)
                            notional = sum(
                                max(0, trade.quantity) * trade.price for trade in matches
                            )
                            avg = notional / filled if filled else 0.0
                            response = BrokerOrderResponse(
                                success=True,
                                broker_order_id=matches[0].broker_order_id,
                                client_order_id=order.client_order_id,
                                status=(
                                    "FILLED"
                                    if filled >= order.quantity
                                    else "PARTIALLY_FILLED"
                                ),
                                filled_quantity=min(filled, order.quantity),
                                average_price=avg,
                                message="Recovered from broker trade reconciliation",
                            )

                if response is None:
                    continue

                normalized_status = self._normalize_broker_status(response.status)
                new_filled = int(response.filled_quantity or 0)
                delta_fill = max(0, new_filled - order.filled_quantity)
                await self.bus.publish(
                    EventEnvelope(
                        topic=Topics.BROKER_ORDER_EVENT,
                        payload={
                            "client_order_id": order.client_order_id,
                            "broker_order_id": (
                                response.broker_order_id or order.broker_order_id
                            ),
                            "status": normalized_status,
                            "filled_quantity": new_filled,
                            "average_price": float(response.average_price or 0.0),
                            "message": response.message or "Broker reconciliation",
                        },
                    )
                )
                if delta_fill > 0:
                    await self._publish_trade_fill(
                        order=order,
                        quantity=delta_fill,
                        price=float(
                            response.average_price
                            or order.average_price
                            or order.price
                        ),
                    )
            except Exception:
                logger.exception(
                    "Live order reconciliation failed for %s", order.order_id
                )

    async def _reconciliation_loop(self, poll_interval_sec: float = 1.0) -> None:
        while self._reconciliation_running:
            try:
                await self.reconcile_live_orders()
                await asyncio.sleep(poll_interval_sec)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Execution reconciliation loop failed")
                await asyncio.sleep(poll_interval_sec)
