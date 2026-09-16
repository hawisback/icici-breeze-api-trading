"""Market Data Service managing live quotes, feed freshness, and event dissemination.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import math
import random
from typing import Any, Optional

from libs.contracts.models import Candle, Quote, utc_now
from libs.events.bus import EventBus, EventEnvelope, Topics, get_event_bus
from services.market_data.candle_builder import CandleBuilder

logger = logging.getLogger(__name__)


class MarketDataService:
    """Ingests market ticks, maintains latest quote cache, builds candles, and monitors feed freshness."""

    def __init__(self, event_bus: Optional[EventBus] = None) -> None:
        self.bus = event_bus or get_event_bus()
        self._quotes: dict[str, Quote] = {}
        self._last_tick_time: Optional[datetime] = None
        self._candle_builder_1m = CandleBuilder(interval_minutes=1)
        self._candle_builder_5m = CandleBuilder(interval_minutes=5)
        self._simulation_task: Optional[asyncio.Task[None]] = None
        self._running: bool = False

    async def initialize(self) -> None:
        # Seed initial baseline quotes for key instruments
        self._seed_initial_quotes()

    def _seed_initial_quotes(self) -> None:
        now = utc_now()
        self.update_quote(
            Quote(
                instrument_id="INST-NIFTY-INDEX",
                symbol="NIFTY 50",
                last_price=24850.50,
                open=24800.0,
                high=24890.0,
                low=24780.0,
                close=24800.0,
                volume=15420000,
                change_pct=0.20,
                timestamp=now,
            )
        )
        self.update_quote(
            Quote(
                instrument_id="INST-BANKNIFTY-INDEX",
                symbol="NIFTY BANK",
                last_price=52450.00,
                open=52300.0,
                high=52600.0,
                low=52250.0,
                close=52300.0,
                volume=8920000,
                change_pct=0.28,
                timestamp=now,
            )
        )

    def update_quote(self, quote: Quote) -> None:
        """Update live quote cache and feed freshness."""
        self._quotes[quote.instrument_id] = quote
        self._quotes[quote.symbol] = quote
        self._last_tick_time = quote.timestamp

    async def ingest_quote(self, quote: Quote) -> None:
        """Ingest live quote, update caches, build candles, and publish event."""
        self.update_quote(quote)

        # Publish quote event
        await self.bus.publish(
            EventEnvelope(
                topic=Topics.MARKET_QUOTE,
                payload=quote.model_dump(),
            )
        )

        # Check candle builder completions
        c1 = self._candle_builder_1m.process_quote(quote)
        if c1:
            await self.bus.publish(
                EventEnvelope(topic=Topics.MARKET_CANDLE, payload=c1.model_dump())
            )
        c5 = self._candle_builder_5m.process_quote(quote)
        if c5:
            await self.bus.publish(
                EventEnvelope(topic=Topics.MARKET_CANDLE, payload=c5.model_dump())
            )

    def get_latest_quote(self, symbol_or_id: str) -> Optional[Quote]:
        return self._quotes.get(symbol_or_id)

    def get_all_quotes(self) -> list[Quote]:
        seen = set()
        unique_quotes = []
        for q in self._quotes.values():
            if q.instrument_id not in seen:
                seen.add(q.instrument_id)
                unique_quotes.append(q)
        return unique_quotes

    def get_feed_status(self) -> dict[str, Any]:
        """Determine feed status: LIVE, STALE, or DOWN."""
        if not self._last_tick_time:
            return {"status": "DOWN", "latency_sec": None, "message": "No ticks received"}

        diff = (utc_now() - self._last_tick_time).total_seconds()
        if diff < 5.0:
            status = "LIVE"
        elif diff < 30.0:
            status = "STALE"
        else:
            status = "DOWN"

        return {
            "status": status,
            "latency_sec": round(diff, 2),
            "last_tick_time": self._last_tick_time.isoformat(),
        }

    async def start_simulated_feed(self, interval_sec: float = 1.0) -> None:
        """Background simulator emitting realistic micro-ticks for paper trading & UI."""
        if self._running:
            return
        self._running = True
        self._simulation_task = asyncio.create_task(self._simulate_ticks_loop(interval_sec))
        logger.info("Market data simulated feed started.")

    async def stop_simulated_feed(self) -> None:
        self._running = False
        if self._simulation_task:
            self._simulation_task.cancel()
            try:
                await self._simulation_task
            except asyncio.CancelledError:
                pass
            self._simulation_task = None
        logger.info("Market data simulated feed stopped.")

    async def _simulate_ticks_loop(self, interval_sec: float) -> None:
        while self._running:
            try:
                for inst_id in ["INST-NIFTY-INDEX", "INST-BANKNIFTY-INDEX"]:
                    q = self.get_latest_quote(inst_id)
                    if not q:
                        continue
                    # Micro-fluctuation
                    delta = (random.random() - 0.49) * 2.5
                    new_price = round(q.last_price + delta, 2)
                    new_quote = Quote(
                        instrument_id=q.instrument_id,
                        symbol=q.symbol,
                        last_price=new_price,
                        open=q.open,
                        high=max(q.high, new_price),
                        low=min(q.low, new_price),
                        close=new_price,
                        volume=q.volume + random.randint(10, 100),
                        change_pct=round(((new_price - q.open) / q.open) * 100, 2),
                        timestamp=utc_now(),
                    )
                    await self.ingest_quote(new_quote)
                await asyncio.sleep(interval_sec)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in simulated tick loop: %s", e)
                await asyncio.sleep(interval_sec)

