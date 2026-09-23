"""Risk Service validating pre-trade risk controls and system safety modes.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any, Optional

from libs.contracts.models import (
    OrderIntent,
    OrderSide,
    RiskDecision,
    SystemMode,
    TradingMode,
    generate_id,
    utc_now,
)
from libs.events.bus import EventBus, EventEnvelope, Topics, get_event_bus
from services.risk.live_gate import LiveTradingGate
from services.risk.repository import RiskRepository

logger = logging.getLogger(__name__)


class RiskService:
    """Evaluates every incoming order intent against pre-trade risk rules."""

    def __init__(
        self,
        repository: Optional[RiskRepository] = None,
        event_bus: Optional[EventBus] = None,
        live_gate: Optional[LiveTradingGate] = None,
        max_order_qty: int = 1800,
        live_account_id: str = "ICICI_PRIMARY",
        portfolio_service: Any = None,
    ) -> None:
        self.repo = repository or RiskRepository()
        self.bus = event_bus or get_event_bus()
        self.live_gate = live_gate or LiveTradingGate(event_bus=self.bus)
        self.max_order_qty = max_order_qty
        self.live_account_id = live_account_id
        self.portfolio_service = portfolio_service
        self._recent_orders: dict[str, datetime] = {}  # symbol:side:qty -> timestamp

    async def initialize(self) -> None:
        await self.repo.initialize()
        # Subscribe to order intents
        await self.bus.subscribe(Topics.ORDER_INTENT, self._handle_order_intent_event)

    def set_portfolio_service(self, portfolio_service: Any) -> None:
        self.portfolio_service = portfolio_service

    async def _handle_order_intent_event(self, envelope: EventEnvelope[Any]) -> None:
        intent = OrderIntent.model_validate(envelope.payload)
        decision = await self.evaluate_intent(intent)
        # Decision is automatically published inside evaluate_intent

    async def evaluate_intent(self, intent: OrderIntent) -> RiskDecision:
        """Run sequential pre-trade checks on intent."""
        system_mode = await self.repo.get_system_mode()

        is_reduce_only_exit = intent.reduce_only and intent.side == OrderSide.SELL

        # Reduce-only is an explicit safety property, never an alias for SELL.
        if intent.reduce_only and intent.side != OrderSide.SELL:
            return await self._record_and_publish(
                intent=intent,
                approved=False,
                rule="INVALID_REDUCE_ONLY",
                reason="Reduce-only orders must be SELL orders in the long-options execution model",
                system_mode=system_mode,
            )

        if is_reduce_only_exit:
            if self.portfolio_service is None:
                return await self._record_and_publish(
                    intent=intent,
                    approved=False,
                    rule="REDUCE_ONLY_POSITION_UNVERIFIED",
                    reason="Reduce-only exit rejected because position service is unavailable",
                    system_mode=system_mode,
                )
            position = await self.portfolio_service.repo.get_position(
                intent.instrument_id
            )
            held_quantity = int(position.quantity) if position is not None else 0
            if held_quantity <= 0 or intent.quantity > held_quantity:
                return await self._record_and_publish(
                    intent=intent,
                    approved=False,
                    rule="REDUCE_ONLY_QUANTITY_EXCEEDED",
                    reason=(
                        f"Reduce-only exit quantity {intent.quantity} exceeds "
                        f"verified long position {max(0, held_quantity)}"
                    ),
                    system_mode=system_mode,
                )

        # LIVE authorization gates exposure increases. Verified reduce-only exits
        # remain available after gate expiry/revocation so emergency controls
        # cannot trap an already-open position.
        if intent.trading_mode == TradingMode.LIVE and not is_reduce_only_exit:
            authorized, reason = self.live_gate.validate_live_order(account_id=self.live_account_id)
            if not authorized:
                return await self._record_and_publish(
                    intent=intent,
                    approved=False,
                    rule="LIVE_TRADING_NOT_AUTHORIZED",
                    reason=f"LIVE order rejected: {reason}",
                    system_mode=system_mode,
                )

        # All emergency modes are entry-blocking. Explicit reduce-only exits
        # remain permitted so HALT/EXIT_ONLY cannot disable risk reduction.
        if system_mode in (SystemMode.HALTED, SystemMode.EXIT_ONLY, SystemMode.ENTRY_BLOCKED) and not is_reduce_only_exit:
            rule = {
                SystemMode.HALTED: "SYSTEM_HALTED",
                SystemMode.EXIT_ONLY: "EXIT_ONLY",
                SystemMode.ENTRY_BLOCKED: "ENTRY_BLOCKED",
            }[system_mode]
            return await self._record_and_publish(
                intent=intent,
                approved=False,
                rule=rule,
                reason=f"System mode {system_mode.value} permits only explicit reduce-only exits",
                system_mode=system_mode,
            )

        # Check 3: Max order quantity limit
        if intent.quantity <= 0 or intent.quantity > self.max_order_qty:
            return await self._record_and_publish(
                intent=intent,
                approved=False,
                rule="MAX_ORDER_QUANTITY",
                reason=f"Order quantity {intent.quantity} violates limits (1 - {self.max_order_qty})",
                system_mode=system_mode,
            )

        # Check 4: Price sanity
        if intent.price <= 0.05:
            return await self._record_and_publish(
                intent=intent,
                approved=False,
                rule="PRICE_SANITY",
                reason=f"Order price {intent.price} is invalid or below tick size",
                system_mode=system_mode,
            )

        # Check 5: Duplicate order protection (within 1 second window)
        dup_key = f"{intent.symbol}:{intent.side.value}:{intent.quantity}:{intent.price}"
        now = utc_now()
        last_seen = self._recent_orders.get(dup_key)
        if last_seen and (now - last_seen).total_seconds() < 1.0:
            return await self._record_and_publish(
                intent=intent,
                approved=False,
                rule="DUPLICATE_ORDER_THROTTLE",
                reason="Identical order submitted within 1 second throttle window",
                system_mode=system_mode,
            )
        self._recent_orders[dup_key] = now

        # All checks passed!
        return await self._record_and_publish(
            intent=intent,
            approved=True,
            rule="ALL_CHECKS_PASSED",
            reason="Pre-trade risk criteria satisfied",
            system_mode=system_mode,
        )

    async def _record_and_publish(
        self,
        intent: OrderIntent,
        approved: bool,
        rule: str,
        reason: str,
        system_mode: SystemMode,
    ) -> RiskDecision:
        decision = RiskDecision(
            decision_id=generate_id(),
            intent_id=intent.intent_id,
            approved=approved,
            rule_name=rule,
            reason=reason,
            system_mode=system_mode,
            evaluated_at=utc_now(),
        )
        await self.repo.save_decision(decision, outbox_topic=Topics.RISK_DECISION)

        # Fast-path event publish
        await self.bus.publish(
            EventEnvelope(
                topic=Topics.RISK_DECISION,
                correlation_id=intent.correlation_id,
                payload=decision.model_dump(),
            )
        )

        logger.info(
            "Risk decision for intent %s: approved=%s rule=%s reason=%s",
            intent.intent_id,
            approved,
            rule,
            reason,
        )
        return decision

    async def trigger_kill_switch(
        self,
        action: str = "BLOCK_ENTRIES",
        reason: str = "Manual operator intervention",
        activated_by: str = "OPERATOR",
    ) -> str:
        """Trigger emergency kill switch."""
        if action == "BLOCK_ENTRIES":
            await self.repo.set_system_mode(SystemMode.ENTRY_BLOCKED)
        elif action == "HALT":
            await self.repo.set_system_mode(SystemMode.HALTED)
        elif action == "EXIT_ONLY":
            await self.repo.set_system_mode(SystemMode.EXIT_ONLY)

        event_id = await self.repo.record_kill_switch(action, reason, activated_by)

        # Broadcast audit event
        await self.bus.publish(
            EventEnvelope(
                topic=Topics.AUDIT_EVENT,
                payload={
                    "event_type": "KILL_SWITCH_ENABLED",
                    "action": action,
                    "reason": reason,
                    "activated_by": activated_by,
                },
            )
        )
        logger.warning("Kill switch triggered: action=%s reason=%s", action, reason)
        return event_id

    async def set_system_mode(self, mode: SystemMode) -> None:
        await self.repo.set_system_mode(mode)
        await self.bus.publish(
            EventEnvelope(
                topic=Topics.AUDIT_EVENT,
                payload={"event_type": "SYSTEM_MODE_CHANGED", "new_mode": mode.value},
            )
        )

    async def get_system_mode(self) -> SystemMode:
        return await self.repo.get_system_mode()

