"""Repository for Historical Service managing candle persistence in SQLite.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from libs.contracts.models import Candle
from libs.database.sqlite import SQLiteConfig, SQLiteEngine


class HistoricalRepository:
    """Stores and indexes historical OHLCV candlestick data."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        path = db_path or Path("data/market/historical.db")
        # Synchronous NORMAL is optimal for high-throughput market data stores
        self.engine = SQLiteEngine(SQLiteConfig(db_path=path, synchronous="NORMAL"))

    async def initialize(self) -> None:
        await self.engine.initialize()
        async with self.engine.connect() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS historical_candles (
                    instrument_id TEXT NOT NULL,
                    interval TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume INTEGER NOT NULL,
                    open_interest INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT 'BREEZE',
                    PRIMARY KEY (instrument_id, interval, start_time)
                );
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_hist_candles_range
                ON historical_candles(instrument_id, interval, start_time ASC);
            """)
            await conn.commit()

    async def save_candles(self, candles: list[Candle]) -> None:
        if not candles:
            return
        async with self.engine.connect() as conn:
            await conn.executemany(
                """
                INSERT OR REPLACE INTO historical_candles (
                    instrument_id, interval, start_time, end_time,
                    open, high, low, close, volume, open_interest, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        c.instrument_id,
                        c.interval,
                        c.start_time.isoformat(),
                        c.end_time.isoformat(),
                        c.open,
                        c.high,
                        c.low,
                        c.close,
                        c.volume,
                        c.open_interest,
                        c.source,
                    )
                    for c in candles
                ],
            )
            await conn.commit()

    async def get_candles(
        self,
        instrument_id: str,
        interval: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 500,
    ) -> list[Candle]:
        async with self.engine.connect() as conn:
            sql = """
                SELECT * FROM historical_candles
                WHERE instrument_id = ? AND interval = ?
            """
            params: list[object] = [instrument_id, interval]
            if start_time:
                sql += " AND start_time >= ?"
                params.append(start_time.isoformat())
            if end_time:
                sql += " AND start_time <= ?"
                params.append(end_time.isoformat())
            sql += " ORDER BY start_time ASC LIMIT ?"
            params.append(limit)

            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
            return [
                Candle(
                    instrument_id=r["instrument_id"],
                    interval=r["interval"],
                    start_time=datetime.fromisoformat(r["start_time"]),
                    end_time=datetime.fromisoformat(r["end_time"]),
                    open=r["open"],
                    high=r["high"],
                    low=r["low"],
                    close=r["close"],
                    volume=r["volume"],
                    open_interest=r["open_interest"],
                    source=r["source"],
                )
                for r in rows
            ]

