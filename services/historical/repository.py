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
            where_clauses = ["instrument_id = ?", "interval = ?"]
            params: list[object] = [instrument_id, interval]
            if start_time:
                where_clauses.append("start_time >= ?")
                params.append(start_time.isoformat())
            if end_time:
                where_clauses.append("start_time <= ?")
                params.append(end_time.isoformat())

            where_str = " AND ".join(where_clauses)
            sql = f"""
                SELECT * FROM (
                    SELECT * FROM historical_candles
                    WHERE {where_str}
                    ORDER BY start_time DESC
                    LIMIT ?
                ) ORDER BY start_time ASC
            """
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

    async def get_latest_candle(
        self,
        instrument_id: str,
        interval: str,
    ) -> Optional[Candle]:
        """Return the most recent candle stored in the repository."""
        async with self.engine.connect() as conn:
            cursor = await conn.execute(
                """
                SELECT * FROM historical_candles
                WHERE instrument_id = ? AND interval = ?
                ORDER BY start_time DESC
                LIMIT 1
                """,
                (instrument_id, interval),
            )
            row = await cursor.fetchone()
            if not row:
                return None
            return Candle(
                instrument_id=row["instrument_id"],
                interval=row["interval"],
                start_time=datetime.fromisoformat(row["start_time"]),
                end_time=datetime.fromisoformat(row["end_time"]),
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row["volume"],
                open_interest=row["open_interest"],
                source=row["source"],
            )

    async def purge_simulated_candles(self, instrument_id: str, interval: Optional[str] = None) -> int:
        """Remove synthetic candles when real broker candles are available."""
        async with self.engine.connect() as conn:
            if interval:
                res = await conn.execute(
                    "DELETE FROM historical_candles WHERE instrument_id = ? AND interval = ? AND source = 'SIMULATED'",
                    (instrument_id, interval),
                )
            else:
                res = await conn.execute(
                    "DELETE FROM historical_candles WHERE instrument_id = ? AND source = 'SIMULATED'",
                    (instrument_id,),
                )
            await conn.commit()
            return res.rowcount

