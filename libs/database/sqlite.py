"""SQLite database management, connection factory, and transaction infrastructure.

Enforces:
- WAL journal mode
- Foreign keys enabled
- Busy timeout = 5000ms
- Synchronous = FULL (for trading state) / NORMAL (for market data)
- Transactional Outbox pattern support
- Idempotent Inbox consumer deduplication support
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

import aiosqlite

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SQLiteConfig:
    db_path: Path
    synchronous: str = "FULL"  # "FULL" for trading state, "NORMAL" for market data
    busy_timeout_ms: int = 5000
    journal_mode: str = "WAL"
    foreign_keys: bool = True


class SQLiteEngine:
    """Manages an isolated SQLite database file with required production PRAGMAs."""

    def __init__(self, config: SQLiteConfig) -> None:
        self.config = config
        self.db_path = config.db_path.resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize database file with required PRAGMAs and core infrastructure tables."""
        if self._initialized:
            return

        async with self.connect() as conn:
            # Create standard Transactional Outbox table
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS outbox_events (
                    event_id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    published INTEGER NOT NULL DEFAULT 0,
                    published_at TEXT
                );
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_outbox_published
                ON outbox_events(published, created_at);
            """)

            # Create standard Idempotent Inbox table for consumer deduplication
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_events (
                    event_id TEXT NOT NULL,
                    consumer_name TEXT NOT NULL,
                    processed_at TEXT NOT NULL,
                    PRIMARY KEY (event_id, consumer_name)
                );
            """)
            await conn.commit()

        self._initialized = True
        logger.info(
            "Initialized SQLite database at %s with journal_mode=%s, sync=%s",
            self.db_path,
            self.config.journal_mode,
            self.config.synchronous,
        )

    @asynccontextmanager
    async def connect(self) -> AsyncGenerator[aiosqlite.Connection, None]:
        """Provide an aiosqlite connection with enforced PRAGMAs."""
        conn = await aiosqlite.connect(str(self.db_path))
        conn.row_factory = aiosqlite.Row
        try:
            # Enforce strict PRAGMAs per call
            await conn.execute(f"PRAGMA journal_mode = {self.config.journal_mode};")
            if self.config.foreign_keys:
                await conn.execute("PRAGMA foreign_keys = ON;")
            await conn.execute(f"PRAGMA busy_timeout = {self.config.busy_timeout_ms};")
            await conn.execute(f"PRAGMA synchronous = {self.config.synchronous};")
            yield conn
        finally:
            await conn.close()


# ==============================================================================
# Transactional Outbox & Inbox Helper Functions
# ==============================================================================


async def add_outbox_event(
    conn: aiosqlite.Connection,
    event_id: str,
    topic: str,
    payload: dict[str, Any],
    created_at: Optional[datetime] = None,
) -> None:
    """Insert an event into the outbox table within the caller's ongoing transaction."""
    dt_str = (created_at or datetime.now(timezone.utc)).isoformat()
    payload_str = json.dumps(payload, default=str)
    await conn.execute(
        """
        INSERT INTO outbox_events (event_id, topic, payload, created_at, published)
        VALUES (?, ?, ?, ?, 0)
        """,
        (event_id, topic, payload_str, dt_str),
    )


async def get_pending_outbox_events(
    conn: aiosqlite.Connection,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Retrieve unpublished outbox events in chronological order."""
    cursor = await conn.execute(
        """
        SELECT event_id, topic, payload, created_at
        FROM outbox_events
        WHERE published = 0
        ORDER BY created_at ASC
        LIMIT ?
        """,
        (limit,),
    )
    rows = await cursor.fetchall()
    return [
        {
            "event_id": row["event_id"],
            "topic": row["topic"],
            "payload": json.loads(row["payload"]),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


async def mark_outbox_event_published(
    conn: aiosqlite.Connection,
    event_id: str,
    published_at: Optional[datetime] = None,
) -> None:
    """Mark an outbox event as published."""
    dt_str = (published_at or datetime.now(timezone.utc)).isoformat()
    await conn.execute(
        """
        UPDATE outbox_events
        SET published = 1, published_at = ?
        WHERE event_id = ?
        """,
        (dt_str, event_id),
    )


async def is_event_processed(
    conn: aiosqlite.Connection,
    event_id: str,
    consumer_name: str,
) -> bool:
    """Check if an event has already been processed by this consumer."""
    cursor = await conn.execute(
        """
        SELECT 1 FROM processed_events
        WHERE event_id = ? AND consumer_name = ?
        """,
        (event_id, consumer_name),
    )
    row = await cursor.fetchone()
    return row is not None


async def record_processed_event(
    conn: aiosqlite.Connection,
    event_id: str,
    consumer_name: str,
    processed_at: Optional[datetime] = None,
) -> None:
    """Record an event as processed in the idempotent consumer inbox."""
    dt_str = (processed_at or datetime.now(timezone.utc)).isoformat()
    await conn.execute(
        """
        INSERT OR IGNORE INTO processed_events (event_id, consumer_name, processed_at)
        VALUES (?, ?, ?)
        """,
        (event_id, consumer_name, dt_str),
    )

