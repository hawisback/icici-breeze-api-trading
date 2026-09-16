"""SQLite-backed Idempotency Engine for API Gateway Command Endpoints.

Protects against duplicate order placement, duplicate risk activations, and race conditions
during network retries or concurrent browser requests.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Optional

from libs.contracts.models import utc_now
from libs.database.sqlite import SQLiteConfig, SQLiteEngine

logger = logging.getLogger(__name__)


def compute_request_hash(user_id: str, method: str, path: str, body_bytes: bytes) -> str:
    """Compute deterministic SHA-256 fingerprint of the request."""
    hasher = hashlib.sha256()
    hasher.update(user_id.encode("utf-8"))
    hasher.update(b":")
    hasher.update(method.upper().encode("utf-8"))
    hasher.update(b":")
    hasher.update(path.encode("utf-8"))
    hasher.update(b":")
    hasher.update(body_bytes)
    return hasher.hexdigest()


class IdempotencyRepository:
    """Manages persistent idempotency locks and cached responses in gateway.db."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.engine = SQLiteEngine(
            SQLiteConfig(
                db_path=self.db_path,
                synchronous="FULL",
                busy_timeout_ms=5000,
            )
        )
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize the idempotency storage table and index."""
        if self._initialized:
            return

        await self.engine.initialize()
        async with self.engine.connect() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS idempotency_records (
                    idempotency_key TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    request_path TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    response_code INTEGER,
                    response_headers TEXT,
                    response_body TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_idempotency_expires
                ON idempotency_records(expires_at);
            """)
            await conn.commit()

        self._initialized = True
        logger.info("Idempotency repository initialized at %s", self.db_path)

    async def claim_or_get(
        self,
        key: str,
        user_id: str,
        request_path: str,
        request_hash: str,
        ttl_seconds: int = 86400,
    ) -> tuple[str, Optional[dict[str, Any]]]:
        """Atomically claim an idempotency key or retrieve previously completed response.

        Returns:
            ("CLAIMED", None): Key claimed, safe to execute.
            ("COMPLETED", cached_record): Already completed with identical request hash.
            ("IN_PROGRESS", None): Request with this key is currently executing.
            ("MISMATCH", None): Key was previously used with a different request payload.
        """
        now = utc_now()
        now_iso = now.isoformat()
        expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()

        async with self.engine.connect() as conn:
            # Check existing record
            cursor = await conn.execute(
                """
                SELECT idempotency_key, user_id, request_path, request_hash, status,
                       response_code, response_headers, response_body, expires_at
                FROM idempotency_records
                WHERE idempotency_key = ?
                """,
                (key,),
            )
            row = await cursor.fetchone()

            if row:
                # Check expiration
                try:
                    exp = datetime.fromisoformat(row["expires_at"])
                    if exp < now:
                        # Expired: delete and re-claim
                        await conn.execute(
                            "DELETE FROM idempotency_records WHERE idempotency_key = ?",
                            (key,),
                        )
                    else:
                        # Validate hash
                        if row["request_hash"] != request_hash:
                            logger.warning(
                                "Idempotency key %s reused with mismatched request hash.", key
                            )
                            return "MISMATCH", None

                        if row["status"] == "COMPLETED":
                            headers = json.loads(row["response_headers"] or "{}")
                            return "COMPLETED", {
                                "response_code": row["response_code"],
                                "response_body": row["response_body"],
                                "response_headers": headers,
                            }
                        elif row["status"] == "IN_PROGRESS":
                            return "IN_PROGRESS", None
                except Exception as e:
                    logger.error("Error evaluating existing idempotency key: %s", e)

            # Not found or expired: claim key
            try:
                await conn.execute(
                    """
                    INSERT INTO idempotency_records (
                        idempotency_key, user_id, request_path, request_hash,
                        status, created_at, expires_at
                    ) VALUES (?, ?, ?, ?, 'IN_PROGRESS', ?, ?)
                    ON CONFLICT(idempotency_key) DO UPDATE SET
                        user_id = excluded.user_id,
                        request_path = excluded.request_path,
                        request_hash = excluded.request_hash,
                        status = 'IN_PROGRESS',
                        response_code = NULL,
                        response_headers = NULL,
                        response_body = NULL,
                        created_at = excluded.created_at,
                        expires_at = excluded.expires_at
                    """,
                    (key, user_id, request_path, request_hash, now_iso, expires_at),
                )
                await conn.commit()
                return "CLAIMED", None
            except Exception as e:
                logger.error("Failed to claim idempotency key %s: %s", key, e)
                return "IN_PROGRESS", None

    async def save_completion(
        self,
        key: str,
        response_code: int,
        response_body: str,
        response_headers: dict[str, str],
    ) -> None:
        """Persist successful execution response for future replay."""
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                UPDATE idempotency_records
                SET status = 'COMPLETED',
                    response_code = ?,
                    response_headers = ?,
                    response_body = ?
                WHERE idempotency_key = ?
                """,
                (response_code, json.dumps(response_headers), response_body, key),
            )
            await conn.commit()

    async def release_claim(self, key: str) -> None:
        """Release claim on error so client can retry cleanly."""
        async with self.engine.connect() as conn:
            await conn.execute(
                "DELETE FROM idempotency_records WHERE idempotency_key = ?",
                (key,),
            )
            await conn.commit()

    async def cleanup_expired(self) -> int:
        """Delete expired idempotency records."""
        now_iso = utc_now().isoformat()
        async with self.engine.connect() as conn:
            cursor = await conn.execute(
                "DELETE FROM idempotency_records WHERE expires_at < ?",
                (now_iso,),
            )
            await conn.commit()
            return cursor.rowcount


import re
from typing import Awaitable, Callable
from fastapi import HTTPException, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from libs.events.bus import EventEnvelope, Topics

IDEMPOTENCY_KEY_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.]{1,128}$")


async def execute_idempotent_command(
    idempotency_key: Optional[str],
    user_id: str,
    method: str,
    path: str,
    payload_dict: dict[str, Any],
    execute_coroutine_fn: Callable[[], Awaitable[dict[str, Any]]],
    idempotency_repo: IdempotencyRepository,
    event_bus: Any,
    ttl_seconds: int = 86400,
) -> Response:
    """Execute a mutating command idempotently or return cached response if key is provided."""
    if not idempotency_key:
        # No idempotency key provided: execute directly
        result = await execute_coroutine_fn()
        return JSONResponse(status_code=200, content=jsonable_encoder(result))

    # 1. Validate key syntax
    if not IDEMPOTENCY_KEY_REGEX.match(idempotency_key):
        raise HTTPException(
            status_code=400,
            detail="Invalid Idempotency-Key format. Must be 1-128 alphanumeric characters, dashes, underscores, or periods.",
        )

    # 2. Compute request hash
    encoded_payload = jsonable_encoder(payload_dict)
    body_bytes = json.dumps(encoded_payload, sort_keys=True).encode("utf-8")
    req_hash = compute_request_hash(user_id=user_id, method=method, path=path, body_bytes=body_bytes)

    # 3. Check / Claim in repository
    claim_status, cached = await idempotency_repo.claim_or_get(
        key=idempotency_key,
        user_id=user_id,
        request_path=path,
        request_hash=req_hash,
        ttl_seconds=ttl_seconds,
    )

    if claim_status == "COMPLETED" and cached:
        cached_body = (
            json.loads(cached["response_body"])
            if isinstance(cached["response_body"], str)
            else cached["response_body"]
        )
        return JSONResponse(
            status_code=cached["response_code"] or 200,
            content=cached_body,
            headers={
                "X-Idempotency-Replay": "true",
                "Idempotency-Key": idempotency_key,
            },
        )
    elif claim_status == "IN_PROGRESS":
        raise HTTPException(
            status_code=409,
            detail="Request with this Idempotency-Key is currently in progress. Please retry shortly.",
        )
    elif claim_status == "MISMATCH":
        try:
            await event_bus.publish(
                EventEnvelope(
                    topic=Topics.AUDIT_EVENT,
                    payload={
                        "event_type": "IDEMPOTENCY_MISMATCH",
                        "idempotency_key": idempotency_key,
                        "user_id": user_id,
                        "path": path,
                        "timestamp": utc_now().isoformat(),
                    },
                )
            )
        except Exception:
            pass
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key was previously used with different request parameters.",
        )

    # 4. Status is CLAIMED: execute operation and persist result
    try:
        result = await execute_coroutine_fn()
        serializable_result = jsonable_encoder(result)
        await idempotency_repo.save_completion(
            key=idempotency_key,
            response_code=200,
            response_body=json.dumps(serializable_result),
            response_headers={"Content-Type": "application/json"},
        )
        return JSONResponse(
            status_code=200,
            content=serializable_result,
            headers={"Idempotency-Key": idempotency_key},
        )
    except Exception:
        await idempotency_repo.release_claim(idempotency_key)
        raise
