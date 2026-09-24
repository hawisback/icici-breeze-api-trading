"""Repository for Risk Service managing data/risk/risk.db.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from libs.contracts.models import RiskDecision, SystemMode, generate_id, utc_now
from libs.database.sqlite import (
    SQLiteConfig,
    SQLiteEngine,
    add_outbox_event,
    get_pending_outbox_events,
    mark_outbox_event_published,
)


class RiskRepository:
    """Manages risk rules, decisions, kill switches, and system modes."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        path = db_path or Path("data/risk/risk.db")
        self.engine = SQLiteEngine(SQLiteConfig(db_path=path, synchronous="FULL"))

    async def initialize(self) -> None:
        await self.engine.initialize()
        async with self.engine.connect() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS system_modes (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    mode TEXT NOT NULL DEFAULT 'NORMAL',
                    updated_at TEXT NOT NULL
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS kill_switch_events (
                    event_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL, -- BLOCK_ENTRIES, EXIT_ALL, HALT
                    reason TEXT,
                    activated_by TEXT NOT NULL,
                    activated_at TEXT NOT NULL
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS risk_decisions (
                    decision_id TEXT PRIMARY KEY,
                    intent_id TEXT NOT NULL,
                    approved INTEGER NOT NULL,
                    rule_name TEXT,
                    reason TEXT,
                    system_mode TEXT NOT NULL,
                    evaluated_at TEXT NOT NULL
                );
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_risk_intent
                ON risk_decisions(intent_id);
            """)

            # Ensure baseline system mode exists
            await conn.execute("""
                INSERT OR IGNORE INTO system_modes (id, mode, updated_at)
                VALUES (1, 'NORMAL', ?)
            """, (utc_now().isoformat(),))
            await conn.commit()

    async def get_system_mode(self) -> SystemMode:
        async with self.engine.connect() as conn:
            cursor = await conn.execute("SELECT mode FROM system_modes WHERE id = 1")
            row = await cursor.fetchone()
            if row:
                return SystemMode(row["mode"])
            return SystemMode.NORMAL

    async def set_system_mode(self, mode: SystemMode) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(
                "UPDATE system_modes SET mode = ?, updated_at = ? WHERE id = 1",
                (mode.value, utc_now().isoformat()),
            )
            await conn.commit()

    async def record_kill_switch(self, action: str, reason: str, activated_by: str) -> str:
        event_id = generate_id()
        now = utc_now().isoformat()
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT INTO kill_switch_events (event_id, action, reason, activated_by, activated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (event_id, action, reason, activated_by, now),
            )
            await conn.commit()
        return event_id

    async def save_decision(self, decision: RiskDecision, outbox_topic: Optional[str] = None) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT INTO risk_decisions (
                    decision_id, intent_id, approved, rule_name, reason, system_mode, evaluated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.decision_id,
                    decision.intent_id,
                    1 if decision.approved else 0,
                    decision.rule_name,
                    decision.reason,
                    decision.system_mode.value,
                    decision.evaluated_at.isoformat(),
                ),
            )
            if outbox_topic:
                await add_outbox_event(
                    conn=conn,
                    event_id=decision.decision_id,
                    topic=outbox_topic,
                    payload=decision.model_dump(),
                    created_at=decision.evaluated_at,
                )
            await conn.commit()

    async def get_outbox_events_to_publish(
        self,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            return await get_pending_outbox_events(conn, limit)

    async def mark_outbox_published(self, event_id: str) -> None:
        async with self.engine.connect() as conn:
            await mark_outbox_event_published(conn, event_id)
            await conn.commit()

    async def get_decision_by_intent(self, intent_id: str) -> Optional[RiskDecision]:
        async with self.engine.connect() as conn:
            cursor = await conn.execute(
                "SELECT * FROM risk_decisions WHERE intent_id = ? ORDER BY evaluated_at DESC LIMIT 1",
                (intent_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return RiskDecision(
                decision_id=row["decision_id"],
                intent_id=row["intent_id"],
                approved=bool(row["approved"]),
                rule_name=row["rule_name"],
                reason=row["reason"],
                system_mode=SystemMode(row["system_mode"]),
                evaluated_at=datetime.fromisoformat(row["evaluated_at"]),
            )

