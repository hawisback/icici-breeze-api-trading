"""Repository for Audit Service managing data/audit/audit.db.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Optional

from libs.contracts.models import generate_id, utc_now
from libs.database.sqlite import SQLiteConfig, SQLiteEngine


class AuditRepository:
    """Manages append-only immutable audit log in SQLite."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        path = db_path or Path("data/audit/audit.db")
        self.engine = SQLiteEngine(SQLiteConfig(db_path=path, synchronous="FULL"))

    async def initialize(self) -> None:
        await self.engine.initialize()
        async with self.engine.connect() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_time
                ON audit_events(occurred_at DESC);
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_audit_corr
                ON audit_events(correlation_id);
            """)
            await conn.commit()

    async def append_event(
        self,
        event_type: str,
        correlation_id: str,
        source: str,
        payload: dict[str, Any],
        occurred_at: Optional[datetime] = None,
    ) -> None:
        dt_str = (occurred_at or utc_now()).isoformat()
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT INTO audit_events (
                    event_id, event_type, correlation_id, source, payload, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    generate_id(),
                    event_type,
                    correlation_id,
                    source,
                    json.dumps(payload, default=str),
                    dt_str,
                ),
            )
            await conn.commit()

    async def list_recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            cursor = await conn.execute(
                """
                SELECT event_id, event_type, correlation_id, source, payload, occurred_at
                FROM audit_events
                ORDER BY occurred_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
            return [
                {
                    "event_id": r["event_id"],
                    "event_type": r["event_type"],
                    "correlation_id": r["correlation_id"],
                    "source": r["source"],
                    "payload": json.loads(r["payload"]),
                    "occurred_at": r["occurred_at"],
                }
                for r in rows
            ]

