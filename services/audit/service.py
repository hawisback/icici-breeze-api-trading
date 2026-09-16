"""Audit Service recording append-only audit trail from domain events.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from libs.events.bus import EventBus, EventEnvelope, Topics, get_event_bus
from services.audit.repository import AuditRepository

logger = logging.getLogger(__name__)


class AuditService:
    """Subscribes to audit and trading events to persist immutable audit trails."""

    def __init__(
        self,
        repository: Optional[AuditRepository] = None,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self.repo = repository or AuditRepository()
        self.bus = event_bus or get_event_bus()

    async def initialize(self) -> None:
        await self.repo.initialize()
        await self.bus.subscribe(Topics.AUDIT_EVENT, self._handle_audit_event)
        await self.bus.subscribe(Topics.RISK_DECISION, self._handle_risk_event)

    async def _handle_audit_event(self, envelope: EventEnvelope[Any]) -> None:
        payload = envelope.payload
        event_type = payload.get("event_type", "GENERIC_AUDIT")
        await self.repo.append_event(
            event_type=event_type,
            correlation_id=envelope.correlation_id,
            source=payload.get("source", "SYSTEM"),
            payload=payload,
            occurred_at=envelope.occurred_at,
        )

    async def _handle_risk_event(self, envelope: EventEnvelope[Any]) -> None:
        payload = envelope.payload
        decision_type = "RISK_APPROVED" if payload.get("approved") else "RISK_REJECTED"
        await self.repo.append_event(
            event_type=decision_type,
            correlation_id=envelope.correlation_id,
            source="RISK_SERVICE",
            payload=payload,
            occurred_at=envelope.occurred_at,
        )

    async def record_manual_action(
        self,
        event_type: str,
        correlation_id: str,
        source: str,
        details: dict[str, Any],
    ) -> None:
        """Explicit entry point for user UI actions (e.g. Kill Switch trigger)."""
        await self.repo.append_event(
            event_type=event_type,
            correlation_id=correlation_id,
            source=source,
            payload=details,
        )

    async def get_recent_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        return await self.repo.list_recent_events(limit=limit)

