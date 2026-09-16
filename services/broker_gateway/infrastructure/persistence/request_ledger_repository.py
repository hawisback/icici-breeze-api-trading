"""Broker Write Request Ledger Repository backed by SQLite broker_gateway.db.

Implements durable write idempotency, response caching, audit tracking,
and state transition enforcement (RECEIVED -> SUBMITTING -> ACKNOWLEDGED / SUBMISSION_UNKNOWN / REJECTED).
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, Optional
import uuid

from libs.config.settings import get_settings
from libs.database.sqlite import SQLiteConfig, SQLiteEngine
from services.broker_gateway.domain.enums import BrokerWriteAction, BrokerWriteStatus
from services.broker_gateway.domain.errors import (
    BrokerBaseError,
    BrokerOrderRejectedError,
    BrokerSubmissionUnknownError,
    BrokerValidationError,
)
from services.broker_gateway.domain.models.orders import BrokerOrderAcknowledgement
from services.broker_gateway.domain.ports.request_ledger_port import BrokerRequestLedgerPort

logger = logging.getLogger(__name__)


class BrokerRequestLedgerRepository(BrokerRequestLedgerPort):
    """Repository managing durable write idempotency and call audit in broker_gateway.db."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        path = db_path or get_settings().broker_gateway_db_path
        self.engine = SQLiteEngine(SQLiteConfig(db_path=path, synchronous="FULL"))

    async def initialize(self) -> None:
        """Ensure database engine and tables are initialized."""
        await self.engine.initialize()

    async def record_intent(
        self,
        request_id: str,
        account_id: str,
        client_reference: str,
        action: BrokerWriteAction,
        request_hash: str,
        payload: dict[str, Any],
    ) -> tuple[bool, Optional[BrokerOrderAcknowledgement]]:
        """Reserve request_id or return cached acknowledgement if already completed.

        Returns:
            (True, None) if request is brand new and reserved.
            (False, ack) if request was previously completed (idempotent replay).

        Raises:
            BrokerValidationError: If request_id exists with differing payload hash.
            BrokerSubmissionUnknownError: If previous execution timed out or was indeterminate.
            BrokerBaseError: If request is currently inflight.
            BrokerOrderRejectedError: If previous execution was rejected by broker.
        """
        async with self.engine.connect() as conn:
            cursor = await conn.execute(
                """
                SELECT request_id, account_id, client_reference, request_hash,
                       action, state, broker_order_id, result_json, error_message
                FROM broker_write_requests
                WHERE request_id = ?
                """,
                (request_id,),
            )
            row = await cursor.fetchone()

            if row:
                stored_hash = row[3]
                state = row[5]
                broker_order_id = row[6]
                result_json = row[7]
                error_msg = row[8]

                if stored_hash != request_hash:
                    raise BrokerValidationError(
                        f"Idempotency key collision: request_id '{request_id}' already exists "
                        f"with differing payload hash."
                    )

                if state == BrokerWriteStatus.ACKNOWLEDGED.value:
                    ack_data = json.loads(result_json) if result_json else {}
                    ack_dt_str = ack_data.get("acknowledged_at")
                    ack_dt = (
                        datetime.fromisoformat(ack_dt_str)
                        if ack_dt_str
                        else datetime.now(timezone.utc)
                    )
                    ack = BrokerOrderAcknowledgement(
                        request_id=request_id,
                        client_reference=row[2],
                        broker_order_id=broker_order_id,
                        status=BrokerWriteStatus.ACKNOWLEDGED,
                        message=ack_data.get("message", "Order acknowledged (cached replay)"),
                        acknowledged_at=ack_dt,
                    )
                    logger.info("Idempotent replay of ACKNOWLEDGED request %s", request_id)
                    return False, ack

                if state == BrokerWriteStatus.SUBMISSION_UNKNOWN.value:
                    raise BrokerSubmissionUnknownError(
                        f"Request '{request_id}' previously ended in SUBMISSION_UNKNOWN: {error_msg}. "
                        f"Reconciliation required before retry."
                    )

                if state == BrokerWriteStatus.SUBMITTING.value:
                    raise BrokerBaseError(
                        f"Request '{request_id}' is currently in-flight to broker. Cannot retry."
                    )

                if state == BrokerWriteStatus.REJECTED.value:
                    raise BrokerOrderRejectedError(
                        f"Request '{request_id}' was rejected by broker: {error_msg}"
                    )

                if state == BrokerWriteStatus.RECEIVED.value:
                    # Recorded but not yet submitted; allow continuation
                    return True, None

            # New request reservation
            now_iso = datetime.now(timezone.utc).isoformat()
            act_val = action.value if hasattr(action, "value") else str(action)
            await conn.execute(
                """
                INSERT INTO broker_write_requests (
                    request_id, account_id, client_reference, request_hash,
                    action, state, broker_order_id, payload_json,
                    result_json, error_message, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL, ?, ?)
                """,
                (
                    request_id,
                    account_id,
                    client_reference,
                    request_hash,
                    act_val,
                    BrokerWriteStatus.RECEIVED.value,
                    json.dumps(payload, default=str),
                    now_iso,
                    now_iso,
                ),
            )
            await conn.commit()
            return True, None

    async def mark_submitting(self, request_id: str) -> None:
        """Mark request as currently inflight to broker."""
        now_iso = datetime.now(timezone.utc).isoformat()
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                UPDATE broker_write_requests
                SET state = ?, updated_at = ?
                WHERE request_id = ?
                """,
                (BrokerWriteStatus.SUBMITTING.value, now_iso, request_id),
            )
            await conn.commit()

    async def mark_acknowledged(
        self,
        request_id: str,
        broker_order_id: Optional[str],
        result: dict[str, Any],
    ) -> None:
        """Record successful broker acknowledgement."""
        now_iso = datetime.now(timezone.utc).isoformat()
        res_dict = dict(result)
        if "acknowledged_at" not in res_dict:
            res_dict["acknowledged_at"] = now_iso

        async with self.engine.connect() as conn:
            await conn.execute(
                """
                UPDATE broker_write_requests
                SET state = ?, broker_order_id = ?, result_json = ?, updated_at = ?
                WHERE request_id = ?
                """,
                (
                    BrokerWriteStatus.ACKNOWLEDGED.value,
                    broker_order_id,
                    json.dumps(res_dict, default=str),
                    now_iso,
                    request_id,
                ),
            )
            await conn.commit()

    async def mark_unknown(
        self,
        request_id: str,
        error_message: str,
    ) -> None:
        """Record ambiguous transport result (timeout/disconnect). Requires reconciliation."""
        now_iso = datetime.now(timezone.utc).isoformat()
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                UPDATE broker_write_requests
                SET state = ?, error_message = ?, updated_at = ?
                WHERE request_id = ?
                """,
                (BrokerWriteStatus.SUBMISSION_UNKNOWN.value, error_message, now_iso, request_id),
            )
            await conn.commit()

    async def mark_rejected(
        self,
        request_id: str,
        rejection_reason: str,
    ) -> None:
        """Record definite broker rejection."""
        now_iso = datetime.now(timezone.utc).isoformat()
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                UPDATE broker_write_requests
                SET state = ?, error_message = ?, updated_at = ?
                WHERE request_id = ?
                """,
                (BrokerWriteStatus.REJECTED.value, rejection_reason, now_iso, request_id),
            )
            await conn.commit()

    async def get_request(self, request_id: str) -> Optional[dict[str, Any]]:
        """Fetch request record by ID."""
        async with self.engine.connect() as conn:
            cursor = await conn.execute(
                """
                SELECT request_id, account_id, client_reference, request_hash,
                       action, state, broker_order_id, payload_json,
                       result_json, error_message, created_at, updated_at
                FROM broker_write_requests
                WHERE request_id = ?
                """,
                (request_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "request_id": row[0],
                "account_id": row[1],
                "client_reference": row[2],
                "request_hash": row[3],
                "action": row[4],
                "state": row[5],
                "broker_order_id": row[6],
                "payload": json.loads(row[7]) if row[7] else {},
                "result": json.loads(row[8]) if row[8] else None,
                "error_message": row[9],
                "created_at": row[10],
                "updated_at": row[11],
            }

    async def record_audit_call(
        self,
        endpoint: str,
        method: str,
        duration_ms: float,
        status_code: Optional[int] = None,
        error_type: Optional[str] = None,
    ) -> None:
        """Record a REST or SDK broker call into broker_call_audit."""
        call_id = str(uuid.uuid4())
        occurred_at = datetime.now(timezone.utc).isoformat()
        try:
            async with self.engine.connect() as conn:
                await conn.execute(
                    """
                    INSERT INTO broker_call_audit (
                        call_id, endpoint, method, duration_ms, status_code, error_type, occurred_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (call_id, endpoint, method, duration_ms, status_code, error_type, occurred_at),
                )
                await conn.commit()
        except Exception as exc:
            logger.warning("Failed to record broker call audit: %s", exc)

