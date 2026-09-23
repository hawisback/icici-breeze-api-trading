"""Repository for Broker Session Service managing data/broker-session/broker_session.db.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Optional

from libs.contracts.models import generate_id, utc_now
from libs.database.sqlite import SQLiteConfig, SQLiteEngine


class BrokerSessionRepository:
    """Manages isolated broker session persistence."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        path = db_path or Path("data/broker-session/broker_session.db")
        self.engine = SQLiteEngine(SQLiteConfig(db_path=path, synchronous="FULL"))

    async def initialize(self) -> None:
        await self.engine.initialize()
        async with self.engine.connect() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS broker_accounts (
                    account_id TEXT PRIMARY KEY,
                    broker_name TEXT NOT NULL,
                    account_name TEXT NOT NULL,
                    api_key TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS session_history (
                    session_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    session_token_masked TEXT NOT NULL,
                    status TEXT NOT NULL, -- ACTIVE, EXPIRED, FAILED
                    login_time TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    metadata TEXT,
                    FOREIGN KEY (account_id) REFERENCES broker_accounts(account_id)
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS health_history (
                    check_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL, -- CONNECTED, DEGRADED, DOWN
                    latency_ms REAL NOT NULL,
                    message TEXT,
                    checked_at TEXT NOT NULL
                );
            """)
            await conn.commit()

    async def save_session(
        self,
        session_id: str,
        account_id: str,
        session_token_masked: str,
        login_time: datetime,
        expires_at: datetime,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT OR IGNORE INTO broker_accounts (
                    account_id, broker_name, account_name, api_key, created_at
                ) VALUES (?, ?, 'Primary Account', '', ?)
                """,
                (
                    account_id,
                    str((metadata or {}).get("broker") or "UNKNOWN").upper(),
                    login_time.isoformat(),
                ),
            )
            await conn.execute(
                """
                INSERT INTO session_history (
                    session_id, account_id, session_token_masked, status,
                    login_time, expires_at, metadata
                ) VALUES (?, ?, ?, 'ACTIVE', ?, ?, ?)
                """,
                (
                    session_id,
                    account_id,
                    session_token_masked,
                    login_time.isoformat(),
                    expires_at.isoformat(),
                    json.dumps(metadata or {}),
                ),
            )
            await conn.commit()

    async def get_active_sessions(self) -> list[dict[str, Any]]:
        """Return all persisted sessions still marked ACTIVE, newest first."""
        async with self.engine.connect() as conn:
            cursor = await conn.execute(
                """
                SELECT session_id, account_id, session_token_masked, status,
                       login_time, expires_at, metadata
                FROM session_history
                WHERE status = 'ACTIVE'
                ORDER BY login_time DESC
                """
            )
            rows = await cursor.fetchall()
            return [
                {
                    "session_id": row["session_id"],
                    "account_id": row["account_id"],
                    "session_token_masked": row["session_token_masked"],
                    "status": row["status"],
                    "login_time": row["login_time"],
                    "expires_at": row["expires_at"],
                    "metadata": json.loads(row["metadata"] or "{}"),
                }
                for row in rows
            ]

    async def get_active_session(self) -> Optional[dict[str, Any]]:
        async with self.engine.connect() as conn:
            cursor = await conn.execute(
                """
                SELECT session_id, account_id, session_token_masked, status,
                       login_time, expires_at, metadata
                FROM session_history
                WHERE status = 'ACTIVE'
                ORDER BY login_time DESC
                LIMIT 1
                """
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "session_id": row["session_id"],
                "account_id": row["account_id"],
                "session_token_masked": row["session_token_masked"],
                "status": row["status"],
                "login_time": row["login_time"],
                "expires_at": row["expires_at"],
                "metadata": json.loads(row["metadata"] or "{}"),
            }

    async def expire_session(self, session_id: str) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(
                "UPDATE session_history SET status = 'EXPIRED' WHERE session_id = ?",
                (session_id,),
            )
            await conn.commit()

    async def record_health_check(
        self,
        status: str,
        latency_ms: float,
        message: Optional[str] = None,
    ) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT INTO health_history (check_id, status, latency_ms, message, checked_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (generate_id(), status, latency_ms, message, utc_now().isoformat()),
            )
            await conn.commit()

    async def get_latest_health(self) -> Optional[dict[str, Any]]:
        async with self.engine.connect() as conn:
            cursor = await conn.execute(
                """
                SELECT status, latency_ms, message, checked_at
                FROM health_history
                ORDER BY checked_at DESC
                LIMIT 1
                """
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "status": row["status"],
                "latency_ms": row["latency_ms"],
                "message": row["message"],
                "checked_at": row["checked_at"],
            }

