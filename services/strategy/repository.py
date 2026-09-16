"""Repository for Strategy Service managing data/strategy/strategy.db.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Optional

from libs.contracts.models import OrderSide, Signal, TradingMode, generate_id, utc_now
from libs.database.sqlite import SQLiteConfig, SQLiteEngine


class StrategyRepository:
    """Manages strategy definitions, instances, parameters, and emitted signals."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        path = db_path or Path("data/strategy/strategy.db")
        self.engine = SQLiteEngine(SQLiteConfig(db_path=path, synchronous="FULL"))

    async def initialize(self) -> None:
        await self.engine.initialize()
        async with self.engine.connect() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS strategy_definitions (
                    definition_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    description TEXT,
                    created_at TEXT NOT NULL
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS strategy_instances (
                    instance_id TEXT PRIMARY KEY,
                    definition_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    mode TEXT NOT NULL, -- SHADOW, PAPER, LIVE
                    symbol TEXT NOT NULL,
                    parameters TEXT NOT NULL,
                    status TEXT NOT NULL, -- RUNNING, STOPPED
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (definition_id) REFERENCES strategy_definitions(definition_id)
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS signals (
                    signal_id TEXT PRIMARY KEY,
                    strategy_instance_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    suggested_price REAL,
                    suggested_quantity INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    metadata TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (strategy_instance_id) REFERENCES strategy_instances(instance_id)
                );
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_instance ON signals(strategy_instance_id);")
            await conn.commit()

    async def save_definition(self, definition_id: str, name: str, version: str, description: str) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO strategy_definitions (definition_id, name, version, description, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (definition_id, name, version, description, utc_now().isoformat()),
            )
            await conn.commit()

    async def save_instance(
        self,
        instance_id: str,
        definition_id: str,
        name: str,
        mode: TradingMode,
        symbol: str,
        parameters: dict[str, Any],
        status: str = "RUNNING",
    ) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO strategy_instances (
                    instance_id, definition_id, name, mode, symbol, parameters, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    instance_id,
                    definition_id,
                    name,
                    mode.value,
                    symbol,
                    json.dumps(parameters),
                    status,
                    utc_now().isoformat(),
                ),
            )
            await conn.commit()

    async def list_instances(self) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            cursor = await conn.execute("SELECT * FROM strategy_instances ORDER BY created_at DESC")
            rows = await cursor.fetchall()
            return [
                {
                    "instance_id": r["instance_id"],
                    "definition_id": r["definition_id"],
                    "name": r["name"],
                    "mode": r["mode"],
                    "symbol": r["symbol"],
                    "parameters": json.loads(r["parameters"]),
                    "status": r["status"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]

    async def save_signal(self, signal: Signal) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT INTO signals (
                    signal_id, strategy_instance_id, symbol, side, suggested_price,
                    suggested_quantity, confidence, metadata, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.signal_id,
                    signal.strategy_instance_id,
                    signal.symbol,
                    signal.side.value,
                    signal.suggested_price,
                    signal.suggested_quantity,
                    signal.confidence,
                    json.dumps(signal.metadata),
                    signal.created_at.isoformat(),
                ),
            )
            await conn.commit()

