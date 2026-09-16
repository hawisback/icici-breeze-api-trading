"""Historical Service providing candle retrieval, backfill, and synthetic data generation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import math
from typing import Optional

from libs.contracts.models import Candle, utc_now
from services.historical.repository import HistoricalRepository

logger = logging.getLogger(__name__)


class HistoricalService:
    """Provides historical candles, gap detection, and backfilling."""

    def __init__(self, repository: Optional[HistoricalRepository] = None) -> None:
        self.repo = repository or HistoricalRepository()

    async def initialize(self) -> None:
        await self.repo.initialize()

    async def get_candles(
        self,
        instrument_id: str,
        interval: str = "5m",
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 500,
    ) -> list[Candle]:
        candles = await self.repo.get_candles(
            instrument_id=instrument_id,
            interval=interval,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )
        if not candles:
            # Generate realistic synthetic historical candles if store is empty
            candles = await self.generate_synthetic_candles(instrument_id, interval, count=100)
            await self.repo.save_candles(candles)

        return candles

    async def generate_synthetic_candles(
        self,
        instrument_id: str,
        interval: str = "5m",
        count: int = 100,
    ) -> list[Candle]:
        """Generate realistic price series for charting and backtesting foundation."""
        step_minutes = 5
        if interval == "1m":
            step_minutes = 1
        elif interval == "15m":
            step_minutes = 15
        elif interval == "1D":
            step_minutes = 1440

        # Base price around typical NIFTY / BANKNIFTY or Option price
        base_price = 24850.0 if "NIFTY" in instrument_id else 150.0
        now = utc_now()
        candles: list[Candle] = []

        curr_close = base_price
        for i in range(count, 0, -1):
            c_start = now - timedelta(minutes=i * step_minutes)
            c_end = c_start + timedelta(minutes=step_minutes)

            # Random walk variation
            variation = math.sin(i * 0.3) * (base_price * 0.002) + (i % 3 - 1) * (base_price * 0.001)
            c_open = curr_close
            c_close = c_open + variation
            c_high = max(c_open, c_close) + abs(variation) * 0.5
            c_low = min(c_open, c_close) - abs(variation) * 0.5
            c_vol = 1500 + int(abs(variation) * 500)

            candles.append(
                Candle(
                    instrument_id=instrument_id,
                    interval=interval,
                    start_time=c_start,
                    end_time=c_end,
                    open=round(c_open, 2),
                    high=round(c_high, 2),
                    low=round(c_low, 2),
                    close=round(c_close, 2),
                    volume=c_vol,
                    open_interest=50000 + i * 100,
                    source="SIMULATED",
                )
            )
            curr_close = c_close

        return candles

