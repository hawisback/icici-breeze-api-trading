"""Repository for Order Management System managing data/oms/oms.db.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Optional

from libs.contracts.models import (
    BrokerOrder,
    OrderEvent,
    OrderIntent,
    OrderSide,
    OrderState,
    OrderType,
    SourceType,
    TradingMode,
    generate_id,
    utc_now,
)
from libs.database.sqlite import (
    SQLiteConfig,
    SQLiteEngine,
    add_outbox_event,
    get_pending_outbox_events,
    is_event_processed,
    mark_outbox_event_published,
    record_processed_event,
)

CONSUMER_NAME = "oms-service"


class OMSRepository:
    """Manages transactional state and outbox events for orders."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        path = db_path or Path("data/oms/oms.db")
        self.engine = SQLiteEngine(SQLiteConfig(db_path=path, synchronous="FULL"))

    async def initialize(self) -> None:
        await self.engine.initialize()
        async with self.engine.connect() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS order_intents (
                    intent_id TEXT PRIMARY KEY,
                    correlation_id TEXT NOT NULL,
                    strategy_instance_id TEXT,
                    source TEXT NOT NULL,
                    instrument_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL NOT NULL,
                    trigger_price REAL,
                    product TEXT NOT NULL,
                    time_in_force TEXT NOT NULL,
                    trading_mode TEXT NOT NULL,
                    reduce_only INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS broker_orders (
                    order_id TEXT PRIMARY KEY,
                    intent_id TEXT NOT NULL,
                    client_order_id TEXT NOT NULL UNIQUE,
                    broker_order_id TEXT,
                    instrument_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    order_type TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    filled_quantity INTEGER NOT NULL DEFAULT 0,
                    remaining_quantity INTEGER NOT NULL,
                    price REAL NOT NULL,
                    average_price REAL NOT NULL DEFAULT 0.0,
                    status TEXT NOT NULL,
                    status_message TEXT,
                    trading_mode TEXT NOT NULL,
                    reduce_only INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (intent_id) REFERENCES order_intents(intent_id)
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS order_events (
                    event_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    reason TEXT,
                    payload TEXT,
                    occurred_at TEXT NOT NULL,
                    FOREIGN KEY (order_id) REFERENCES broker_orders(order_id)
                );
            """)
            intent_columns = {row["name"] for row in await (await conn.execute("PRAGMA table_info(order_intents)")).fetchall()}
            if "reduce_only" not in intent_columns:
                await conn.execute("ALTER TABLE order_intents ADD COLUMN reduce_only INTEGER NOT NULL DEFAULT 0")
            order_columns = {row["name"] for row in await (await conn.execute("PRAGMA table_info(broker_orders)")).fetchall()}
            if "reduce_only" not in order_columns:
                await conn.execute("ALTER TABLE broker_orders ADD COLUMN reduce_only INTEGER NOT NULL DEFAULT 0")

            # Indexes
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_order_intents_time ON order_intents(created_at);")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON broker_orders(status);")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_client_id ON broker_orders(client_order_id);")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_broker_id ON broker_orders(broker_order_id);")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_order_events_time ON order_events(order_id, occurred_at);")
            await conn.commit()

    async def save_order_intent(self, intent: OrderIntent, outbox_topic: str) -> None:
        """Atomically persist OrderIntent and queue outbox event."""
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT INTO order_intents (
                    intent_id, correlation_id, strategy_instance_id, source,
                    instrument_id, symbol, side, order_type, quantity, price,
                    trigger_price, product, time_in_force, trading_mode, reduce_only, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intent.intent_id,
                    intent.correlation_id,
                    intent.strategy_instance_id,
                    intent.source.value,
                    intent.instrument_id,
                    intent.symbol,
                    intent.side.value,
                    intent.order_type.value,
                    intent.quantity,
                    intent.price,
                    intent.trigger_price,
                    intent.product.value,
                    intent.time_in_force.value,
                    intent.trading_mode.value,
                    1 if intent.reduce_only else 0,
                    intent.created_at.isoformat(),
                ),
            )
            await add_outbox_event(
                conn=conn,
                event_id=generate_id(),
                topic=outbox_topic,
                payload=intent.model_dump(),
                created_at=intent.created_at,
            )
            await conn.commit()

    async def save_broker_order_with_transition(
        self,
        order: BrokerOrder,
        from_state: Optional[OrderState],
        to_state: OrderState,
        reason: Optional[str] = None,
        outbox_topic: Optional[str] = None,
    ) -> None:
        """Atomically upsert broker_order, record order_event, and enqueue outbox event."""
        now = utc_now()
        event_id = generate_id()
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT INTO broker_orders (
                    order_id, intent_id, client_order_id, broker_order_id,
                    instrument_id, symbol, side, order_type, quantity,
                    filled_quantity, remaining_quantity, price, average_price,
                    status, status_message, trading_mode, reduce_only, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_id) DO UPDATE SET
                    broker_order_id = excluded.broker_order_id,
                    filled_quantity = excluded.filled_quantity,
                    remaining_quantity = excluded.remaining_quantity,
                    average_price = excluded.average_price,
                    status = excluded.status,
                    status_message = excluded.status_message,
                    reduce_only = excluded.reduce_only,
                    updated_at = excluded.updated_at;
                """,
                (
                    order.order_id,
                    order.intent_id,
                    order.client_order_id,
                    order.broker_order_id,
                    order.instrument_id,
                    order.symbol,
                    order.side.value,
                    order.order_type.value,
                    order.quantity,
                    order.filled_quantity,
                    order.remaining_quantity,
                    order.price,
                    order.average_price,
                    to_state.value,
                    order.status_message,
                    order.trading_mode.value,
                    1 if order.reduce_only else 0,
                    order.created_at.isoformat(),
                    now.isoformat(),
                ),
            )

            # Record transition in order_events
            await conn.execute(
                """
                INSERT INTO order_events (
                    event_id, order_id, from_state, to_state, reason, payload, occurred_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    order.order_id,
                    from_state.value if from_state else None,
                    to_state.value,
                    reason,
                    json.dumps(order.model_dump(), default=str),
                    now.isoformat(),
                ),
            )

            # Add to outbox if topic requested
            if outbox_topic:
                await add_outbox_event(
                    conn=conn,
                    event_id=event_id,
                    topic=outbox_topic,
                    payload=order.model_dump(),
                    created_at=now,
                )

            await conn.commit()

    async def get_order_by_id(self, order_id: str) -> Optional[BrokerOrder]:
        async with self.engine.connect() as conn:
            cursor = await conn.execute(
                "SELECT * FROM broker_orders WHERE order_id = ?",
                (order_id,),
            )
            row = await cursor.fetchone()
            return self._row_to_broker_order(row) if row else None

    async def get_order_by_client_id(self, client_order_id: str) -> Optional[BrokerOrder]:
        async with self.engine.connect() as conn:
            cursor = await conn.execute(
                "SELECT * FROM broker_orders WHERE client_order_id = ?",
                (client_order_id,),
            )
            row = await cursor.fetchone()
            return self._row_to_broker_order(row) if row else None

    async def list_orders(self, limit: int = 100) -> list[BrokerOrder]:
        async with self.engine.connect() as conn:
            cursor = await conn.execute(
                "SELECT * FROM broker_orders ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return [self._row_to_broker_order(r) for r in rows]

    async def get_outbox_events_to_publish(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self.engine.connect() as conn:
            return await get_pending_outbox_events(conn, limit)

    async def mark_outbox_published(self, event_id: str) -> None:
        async with self.engine.connect() as conn:
            await mark_outbox_event_published(conn, event_id)
            await conn.commit()

    def _row_to_broker_order(self, row: Any) -> BrokerOrder:
        return BrokerOrder(
            order_id=row["order_id"],
            intent_id=row["intent_id"],
            client_order_id=row["client_order_id"],
            broker_order_id=row["broker_order_id"],
            instrument_id=row["instrument_id"],
            symbol=row["symbol"],
            side=OrderSide(row["side"]),
            order_type=OrderType(row["order_type"]),
            quantity=row["quantity"],
            filled_quantity=row["filled_quantity"],
            remaining_quantity=row["remaining_quantity"],
            price=row["price"],
            average_price=row["average_price"],
            status=OrderState(row["status"]),
            status_message=row["status_message"],
            trading_mode=TradingMode(row["trading_mode"]),
            reduce_only=bool(row["reduce_only"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

