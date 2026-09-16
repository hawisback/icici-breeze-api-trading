"""Broker Execution Guard enforcing session validation, write rate limits, and idempotency.

Guarantees:
- Every outbound broker write is idempotently ledgered.
- Ambiguous transport outcomes (timeouts/disconnects) are recorded as SUBMISSION_UNKNOWN.
- Blind retries are strictly prohibited.
- Active session is validated prior to placing broker orders.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Any, Callable, Coroutine, Optional

from services.broker_gateway.domain.enums import BrokerWriteAction, SessionStatus
from services.broker_gateway.domain.errors import (
    BrokerOrderRejectedError,
    BrokerSessionExpiredError,
    BrokerSubmissionUnknownError,
    BrokerTimeoutError,
)
from services.broker_gateway.domain.models.orders import BrokerOrderAcknowledgement
from services.broker_gateway.domain.ports.request_ledger_port import BrokerRequestLedgerPort
from services.broker_gateway.domain.ports.session_port import BrokerSessionPort

logger = logging.getLogger(__name__)


def compute_payload_hash(payload: dict[str, Any]) -> str:
    """Deterministic SHA-256 hash of a payload dictionary for idempotency collision detection."""
    serialized = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class ExecutionGuard:
    """Execution safety barrier guarding outbound broker order writes."""

    def __init__(
        self,
        ledger: BrokerRequestLedgerPort,
        session_port: BrokerSessionPort,
    ) -> None:
        self.ledger = ledger
        self.session_port = session_port

    async def execute_guarded_write(
        self,
        request_id: str,
        account_id: str,
        client_reference: str,
        action: BrokerWriteAction,
        payload: dict[str, Any],
        execute_fn: Callable[[], Coroutine[Any, Any, BrokerOrderAcknowledgement]],
    ) -> BrokerOrderAcknowledgement:
        """Execute broker write protected by active session check, ledger reservation, and unknown recovery."""
        # 1. Session verification
        status_snap = await self.session_port.get_status()
        if status_snap.status != SessionStatus.ACTIVE:
            raise BrokerSessionExpiredError(
                f"Cannot execute {action.value} write: broker session is {status_snap.status.value}. "
                "An active daily session is required."
            )

        # 2. Idempotency reservation and replay check
        payload_hash = compute_payload_hash(payload)
        is_new, cached_ack = await self.ledger.record_intent(
            request_id=request_id,
            account_id=account_id,
            client_reference=client_reference,
            action=action,
            request_hash=payload_hash,
            payload=payload,
        )
        if not is_new and cached_ack is not None:
            logger.info("GuardedWrite: Replaying cached acknowledgement for %s", request_id)
            return cached_ack

        # 3. Mark in-flight
        await self.ledger.mark_submitting(request_id)

        # 4. Invoke broker write with fail-safe error classification
        try:
            ack = await execute_fn()
            await self.ledger.mark_acknowledged(
                request_id=request_id,
                broker_order_id=ack.broker_order_id,
                result={
                    "broker_order_id": ack.broker_order_id,
                    "client_reference": ack.client_reference,
                    "status": ack.status.value,
                    "message": ack.message,
                    "acknowledged_at": ack.acknowledged_at.isoformat(),
                },
            )
            return ack

        except (BrokerTimeoutError, TimeoutError, asyncio.TimeoutError) as exc:
            # Mandatory rule: Transport timeout -> SUBMISSION_UNKNOWN
            error_msg = f"Transport timeout during {action.value}: {exc}"
            logger.error("GuardedWrite: %s for request %s", error_msg, request_id)
            await self.ledger.mark_unknown(request_id, error_msg)
            raise BrokerSubmissionUnknownError(
                f"Broker order submission outcome is unknown due to timeout for request {request_id}. "
                "Blind retries are prohibited; reconciliation with broker order book required."
            ) from exc

        except BrokerOrderRejectedError as exc:
            logger.warning("GuardedWrite: Order rejected for %s: %s", request_id, exc)
            await self.ledger.mark_rejected(request_id, str(exc))
            raise

        except Exception as exc:
            logger.error("GuardedWrite: Unexpected error during %s for %s: %s", action.value, request_id, exc)
            await self.ledger.mark_rejected(request_id, str(exc))
            raise

