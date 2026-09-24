"""Repository for Portfolio Service managing data/portfolio/portfolio.db.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from libs.contracts.models import Execution, OrderSide, PnLSnapshot, Position, TradingMode, generate_id, utc_now
from libs.database.sqlite import SQLiteConfig, SQLiteEngine


class PortfolioRepository:
    """Manages isolated positions, executions, and P&L snapshots."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        path = db_path or Path("data/portfolio/portfolio.db")
        self.engine = SQLiteEngine(SQLiteConfig(db_path=path, synchronous="FULL"))

    async def initialize(self) -> None:
        await self.engine.initialize()
        async with self.engine.connect() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS executions (
                    execution_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    broker_execution_id TEXT,
                    instrument_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL NOT NULL,
                    fee REAL NOT NULL DEFAULT 0.0,
                    execution_time TEXT NOT NULL
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    position_id TEXT PRIMARY KEY,
                    instrument_id TEXT NOT NULL UNIQUE,
                    symbol TEXT NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 0,
                    buy_quantity INTEGER NOT NULL DEFAULT 0,
                    sell_quantity INTEGER NOT NULL DEFAULT 0,
                    buy_value REAL NOT NULL DEFAULT 0.0,
                    sell_value REAL NOT NULL DEFAULT 0.0,
                    average_price REAL NOT NULL DEFAULT 0.0,
                    current_price REAL NOT NULL DEFAULT 0.0,
                    realized_pnl REAL NOT NULL DEFAULT 0.0,
                    unrealized_pnl REAL NOT NULL DEFAULT 0.0,
                    total_pnl REAL NOT NULL DEFAULT 0.0,
                    trading_mode TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS pnl_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    realized_pnl REAL NOT NULL,
                    unrealized_pnl REAL NOT NULL,
                    total_pnl REAL NOT NULL,
                    day_pnl REAL NOT NULL,
                    open_positions_count INTEGER NOT NULL,
                    timestamp TEXT NOT NULL
                );
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_exec_instrument ON executions(instrument_id);")
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_pos_instrument ON positions(instrument_id);")
            await conn.commit()

    async def apply_execution_atomic(
        self,
        execution: Execution,
        *,
        trading_mode: TradingMode,
        current_price: float,
    ) -> tuple[Position, bool]:
        """Atomically dedupe a fill and apply it to the position.

        The execution id is deterministic for broker cumulative-fill progress,
        so replay after restart becomes a no-op instead of duplicating
        quantity/P&L.
        """
        async with self.engine.connect() as conn:
            existing = await (
                await conn.execute(
                    "SELECT execution_id FROM executions WHERE execution_id = ?",
                    (execution.execution_id,),
                )
            ).fetchone()
            if existing:
                row = await (
                    await conn.execute(
                        "SELECT * FROM positions WHERE instrument_id = ?",
                        (execution.instrument_id,),
                    )
                ).fetchone()
                if row is None:
                    raise RuntimeError(
                        "Execution exists but portfolio position is missing"
                    )
                return self._row_to_position(row), False

            row = await (
                await conn.execute(
                    "SELECT * FROM positions WHERE instrument_id = ?",
                    (execution.instrument_id,),
                )
            ).fetchone()

            if row is None:
                position_id = generate_id()
                buy_quantity = 0
                sell_quantity = 0
                buy_value = 0.0
                sell_value = 0.0
            else:
                position_id = row["position_id"]
                buy_quantity = int(row["buy_quantity"])
                sell_quantity = int(row["sell_quantity"])
                buy_value = float(row["buy_value"])
                sell_value = float(row["sell_value"])

            if execution.side == OrderSide.BUY:
                buy_quantity += execution.quantity
                buy_value += execution.quantity * execution.price
            else:
                sell_quantity += execution.quantity
                sell_value += execution.quantity * execution.price

            net_quantity = buy_quantity - sell_quantity
            average_price = (
                buy_value / buy_quantity if buy_quantity > 0 else 0.0
            )
            closed_quantity = min(buy_quantity, sell_quantity)
            average_sell = (
                sell_value / sell_quantity if sell_quantity > 0 else 0.0
            )
            realized_pnl = (
                (average_sell - average_price) * closed_quantity
                if closed_quantity > 0
                else 0.0
            )
            unrealized_pnl = (
                (current_price - average_price) * net_quantity
                if net_quantity != 0
                else 0.0
            )
            total_pnl = round(realized_pnl + unrealized_pnl, 2)
            now = utc_now()

            await conn.execute(
                """
                INSERT INTO executions (
                    execution_id, order_id, broker_execution_id, instrument_id,
                    symbol, side, quantity, price, fee, execution_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution.execution_id,
                    execution.order_id,
                    execution.broker_execution_id,
                    execution.instrument_id,
                    execution.symbol,
                    execution.side.value,
                    execution.quantity,
                    execution.price,
                    execution.fee,
                    execution.execution_time.isoformat(),
                ),
            )
            await conn.execute(
                """
                INSERT INTO positions (
                    position_id, instrument_id, symbol, quantity, buy_quantity,
                    sell_quantity, buy_value, sell_value, average_price,
                    current_price, realized_pnl, unrealized_pnl, total_pnl,
                    trading_mode, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(instrument_id) DO UPDATE SET
                    symbol = excluded.symbol,
                    quantity = excluded.quantity,
                    buy_quantity = excluded.buy_quantity,
                    sell_quantity = excluded.sell_quantity,
                    buy_value = excluded.buy_value,
                    sell_value = excluded.sell_value,
                    average_price = excluded.average_price,
                    current_price = excluded.current_price,
                    realized_pnl = excluded.realized_pnl,
                    unrealized_pnl = excluded.unrealized_pnl,
                    total_pnl = excluded.total_pnl,
                    trading_mode = excluded.trading_mode,
                    updated_at = excluded.updated_at
                """,
                (
                    position_id,
                    execution.instrument_id,
                    execution.symbol,
                    net_quantity,
                    buy_quantity,
                    sell_quantity,
                    buy_value,
                    sell_value,
                    round(average_price, 2),
                    round(current_price, 2),
                    round(realized_pnl, 2),
                    round(unrealized_pnl, 2),
                    total_pnl,
                    trading_mode.value,
                    now.isoformat(),
                ),
            )
            await conn.commit()

            position = Position(
                position_id=position_id,
                instrument_id=execution.instrument_id,
                symbol=execution.symbol,
                quantity=net_quantity,
                buy_quantity=buy_quantity,
                sell_quantity=sell_quantity,
                buy_value=buy_value,
                sell_value=sell_value,
                average_price=round(average_price, 2),
                current_price=round(current_price, 2),
                realized_pnl=round(realized_pnl, 2),
                unrealized_pnl=round(unrealized_pnl, 2),
                total_pnl=total_pnl,
                trading_mode=trading_mode,
                updated_at=now,
            )
            return position, True

    async def save_execution(self, execution: Execution) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT INTO executions (
                    execution_id, order_id, broker_execution_id, instrument_id,
                    symbol, side, quantity, price, fee, execution_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution.execution_id,
                    execution.order_id,
                    execution.broker_execution_id,
                    execution.instrument_id,
                    execution.symbol,
                    execution.side.value,
                    execution.quantity,
                    execution.price,
                    execution.fee,
                    execution.execution_time.isoformat(),
                ),
            )
            await conn.commit()

    async def get_executed_quantity_for_order(self, order_id: str) -> int:
        async with self.engine.connect() as conn:
            row = await (
                await conn.execute(
                    """
                    SELECT COALESCE(SUM(quantity), 0) AS executed_quantity
                    FROM executions
                    WHERE order_id = ?
                    """,
                    (order_id,),
                )
            ).fetchone()
            return int(row["executed_quantity"] or 0)

    async def get_position(self, instrument_id: str) -> Optional[Position]:
        async with self.engine.connect() as conn:
            cursor = await conn.execute(
                "SELECT * FROM positions WHERE instrument_id = ?",
                (instrument_id,),
            )
            row = await cursor.fetchone()
            return self._row_to_position(row) if row else None

    async def list_positions(self) -> list[Position]:
        async with self.engine.connect() as conn:
            cursor = await conn.execute("SELECT * FROM positions ORDER BY symbol ASC")
            rows = await cursor.fetchall()
            return [self._row_to_position(r) for r in rows]

    async def save_position(self, pos: Position) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO positions (
                    position_id, instrument_id, symbol, quantity, buy_quantity,
                    sell_quantity, buy_value, sell_value, average_price,
                    current_price, realized_pnl, unrealized_pnl, total_pnl,
                    trading_mode, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pos.position_id,
                    pos.instrument_id,
                    pos.symbol,
                    pos.quantity,
                    pos.buy_quantity,
                    pos.sell_quantity,
                    pos.buy_value,
                    pos.sell_value,
                    pos.average_price,
                    pos.current_price,
                    pos.realized_pnl,
                    pos.unrealized_pnl,
                    pos.total_pnl,
                    pos.trading_mode.value,
                    pos.updated_at.isoformat(),
                ),
            )
            await conn.commit()

    async def save_pnl_snapshot(self, snapshot: PnLSnapshot) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT INTO pnl_snapshots (
                    snapshot_id, realized_pnl, unrealized_pnl, total_pnl,
                    day_pnl, open_positions_count, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.realized_pnl,
                    snapshot.unrealized_pnl,
                    snapshot.total_pnl,
                    snapshot.day_pnl,
                    snapshot.open_positions_count,
                    snapshot.timestamp.isoformat(),
                ),
            )
            await conn.commit()

    def _row_to_position(self, row: Any) -> Position:
        return Position(
            position_id=row["position_id"],
            instrument_id=row["instrument_id"],
            symbol=row["symbol"],
            quantity=row["quantity"],
            buy_quantity=row["buy_quantity"],
            sell_quantity=row["sell_quantity"],
            buy_value=row["buy_value"],
            sell_value=row["sell_value"],
            average_price=row["average_price"],
            current_price=row["current_price"],
            realized_pnl=row["realized_pnl"],
            unrealized_pnl=row["unrealized_pnl"],
            total_pnl=row["total_pnl"],
            trading_mode=TradingMode(row["trading_mode"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

