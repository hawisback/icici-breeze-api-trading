"""Repository for Instrument Service managing data/instruments/instruments.db.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Optional

from libs.contracts.models import Instrument, OptionRight, generate_id, utc_now
from libs.database.sqlite import SQLiteConfig, SQLiteEngine


class InstrumentRepository:
    """Manages isolated instruments database."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        path = db_path or Path("data/instruments/instruments.db")
        self.engine = SQLiteEngine(SQLiteConfig(db_path=path, synchronous="FULL"))

    async def initialize(self) -> None:
        await self.engine.initialize()
        async with self.engine.connect() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS instruments (
                    instrument_id TEXT PRIMARY KEY,
                    broker TEXT NOT NULL DEFAULT 'ICICI_BREEZE',
                    exchange TEXT NOT NULL,
                    segment TEXT NOT NULL,
                    underlying TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    expiry TEXT,
                    strike REAL,
                    option_right TEXT,
                    lot_size INTEGER NOT NULL DEFAULT 1,
                    tick_size REAL NOT NULL DEFAULT 0.05,
                    broker_token TEXT,
                    tradable INTEGER NOT NULL DEFAULT 1,
                    valid_from TEXT,
                    valid_to TEXT
                );
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_inst_lookup
                ON instruments(underlying, expiry, strike, option_right);
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_inst_stock
                ON instruments(stock_code);
            """)
            await conn.commit()

    async def save_instrument(self, inst: Instrument) -> None:
        async with self.engine.connect() as conn:
            await conn.execute(
                """
                INSERT OR REPLACE INTO instruments (
                    instrument_id, broker, exchange, segment, underlying, stock_code,
                    expiry, strike, option_right, lot_size, tick_size, broker_token,
                    tradable, valid_from, valid_to
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    inst.instrument_id,
                    inst.broker,
                    inst.exchange,
                    inst.segment,
                    inst.underlying,
                    inst.stock_code,
                    inst.expiry,
                    inst.strike,
                    inst.option_right.value if inst.option_right else None,
                    inst.lot_size,
                    inst.tick_size,
                    inst.broker_token,
                    1 if inst.tradable else 0,
                    inst.valid_from.isoformat() if inst.valid_from else None,
                    inst.valid_to.isoformat() if inst.valid_to else None,
                ),
            )
            await conn.commit()

    async def get_by_id(self, instrument_id: str) -> Optional[Instrument]:
        async with self.engine.connect() as conn:
            cursor = await conn.execute(
                "SELECT * FROM instruments WHERE instrument_id = ?",
                (instrument_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return self._row_to_model(row)

    async def get_by_symbol(self, stock_code: str) -> Optional[Instrument]:
        async with self.engine.connect() as conn:
            cursor = await conn.execute(
                "SELECT * FROM instruments WHERE stock_code = ? LIMIT 1",
                (stock_code,),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return self._row_to_model(row)

    async def search(
        self,
        query: str,
        underlying: Optional[str] = None,
        limit: int = 50,
    ) -> list[Instrument]:
        async with self.engine.connect() as conn:
            sql = "SELECT * FROM instruments WHERE (stock_code LIKE ? OR underlying LIKE ?)"
            params: list[Any] = [f"%{query}%", f"%{query}%"]
            if underlying:
                sql += " AND underlying = ?"
                params.append(underlying)
            sql += " LIMIT ?"
            params.append(limit)

            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
            return [self._row_to_model(r) for r in rows]

    async def get_expiries(self, underlying: str) -> list[str]:
        async with self.engine.connect() as conn:
            cursor = await conn.execute(
                """
                SELECT DISTINCT expiry FROM instruments
                WHERE underlying = ? AND expiry IS NOT NULL AND segment = 'OPTIONS' AND tradable = 1
                ORDER BY expiry ASC
                """,
                (underlying,),
            )
            rows = await cursor.fetchall()
            return [r["expiry"] for r in rows if r["expiry"]]

    async def get_option_chain_instruments(
        self,
        underlying: str,
        expiry: str,
    ) -> list[Instrument]:
        async with self.engine.connect() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM instruments
                WHERE underlying = ? AND expiry = ? AND segment = 'OPTIONS' AND tradable = 1
                ORDER BY strike ASC, option_right ASC
                """,
                (underlying, expiry),
            )
            rows = await cursor.fetchall()
            return [self._row_to_model(r) for r in rows]

    def _row_to_model(self, row: Any) -> Instrument:
        opt_right = None
        if row["option_right"]:
            opt_right = OptionRight(row["option_right"])
        return Instrument(
            instrument_id=row["instrument_id"],
            broker=row["broker"],
            exchange=row["exchange"],
            segment=row["segment"],
            underlying=row["underlying"],
            stock_code=row["stock_code"],
            expiry=row["expiry"],
            strike=row["strike"],
            option_right=opt_right,
            lot_size=row["lot_size"],
            tick_size=row["tick_size"],
            broker_token=row["broker_token"],
            tradable=bool(row["tradable"]),
        )
