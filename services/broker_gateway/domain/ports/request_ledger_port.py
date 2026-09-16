"""Broker Request Ledger Port Protocol.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol

from services.broker_gateway.domain.enums import BrokerWriteAction, BrokerWriteStatus
from services.broker_gateway.domain.models.orders import BrokerOrderAcknowledgement


class BrokerRequestLedgerPort(Protocol):
    """Port for persistent tracking and idempotency of outbound broker write commands."""

    async def record_intent(
        self,
        request_id: str,
        account_id: str,
        client_reference: str,
        action: BrokerWriteAction,
        request_hash: str,
        payload: dict[str, Any],
    ) -> tuple[bool, Optional[BrokerOrderAcknowledgement]]:
        """Reserve request_id or return cached acknowledgement if already completed."""
        ...

    async def mark_submitting(self, request_id: str) -> None:
        """Mark request as currently inflight to broker."""
        ...

    async def mark_acknowledged(
        self,
        request_id: str,
        broker_order_id: Optional[str],
        result: dict[str, Any],
    ) -> None:
        """Record successful broker acknowledgement."""
        ...

    async def mark_unknown(
        self,
        request_id: str,
        error_message: str,
    ) -> None:
        """Record ambiguous transport result (timeout/disconnect). Requires reconciliation."""
        ...

    async def mark_rejected(
        self,
        request_id: str,
        rejection_reason: str,
    ) -> None:
        """Record definite broker rejection."""
        ...

    async def get_request(self, request_id: str) -> Optional[dict[str, Any]]:
        """Fetch request record by ID."""
        ...

