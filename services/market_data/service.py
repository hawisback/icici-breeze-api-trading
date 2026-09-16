"""Market Data Service managing live quotes, feed freshness, and event dissemination.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
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

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        broker_gateway: Optional[Any] = None,
    ) -> None:
        self.bus = event_bus or get_event_bus()
        self.broker_gateway = broker_gateway
        self._quotes: dict[str, Quote] = {}
        self._last_tick_time: Optional[datetime] = None
        self._candle_builder_1m = CandleBuilder(interval_minutes=1)
        self._candle_builder_5m = CandleBuilder(interval_minutes=5)
        self._simulation_task: Optional[asyncio.Task[None]] = None
        self._running: bool = False

    def set_broker_gateway(self, broker_gateway: Any) -> None:
        self.broker_gateway = broker_gateway

    async def initialize(self) -> None:
        # Seed initial baseline quotes with real market closing data
        self._seed_initial_quotes()

    def _seed_initial_quotes(self) -> None:
        now = utc_now()
        # Official closing levels from ICICI Direct / NSE
        self.update_quote(
            Quote(
                instrument_id="INST-NIFTY-INDEX",
                symbol="NIFTY 50",
                last_price=23217.60,
                open=23270.90,
                high=23284.75,
                low=23137.80,
                close=23217.60,
                volume=15420000,
                change_pct=-0.23,
                timestamp=now,
            )
        )
        self.update_quote(
            Quote(
                instrument_id="INST-BANKNIFTY-INDEX",
                symbol="NIFTY BANK",
                last_price=56292.45,
                open=55943.55,
                high=56350.00,
                low=55702.65,
                close=56292.45,
                volume=8920000,
                change_pct=0.62,
                timestamp=now,
            )
        )

    async def sync_quotes_from_broker(self) -> None:
        """Fetch latest quotes from broker or latest candle to ensure price accuracy."""
        if not self.broker_gateway:
            return

        breeze_adapter = getattr(self.broker_gateway, "breeze_adapter", None)
        if not breeze_adapter or not hasattr(breeze_adapter, "client_manager"):
            return

        client_mgr = breeze_adapter.client_manager
        if not client_mgr.is_active:
            return

        logger.info("Syncing latest market quotes from Breeze broker gateway...")
        try:
            sdk = client_mgr.get_sdk_client()
            now = utc_now()
            from_dt = (now - timedelta(days=2)).strftime("%Y-%m-%dT09:15:00.000Z")
            to_dt = now.strftime("%Y-%m-%dT15:30:00.000Z")

            for inst_id, symbol, code in [
                ("INST-NIFTY-INDEX", "NIFTY 50", "NIFTY"),
                ("INST-BANKNIFTY-INDEX", "NIFTY BANK", "CNXBAN"),
            ]:
                raw_res = await client_mgr.sdk_runner.run(
                    lambda: sdk.get_historical_data_v2(
                        interval="5minute",
                        from_date=from_dt,
                        to_date=to_dt,
                        stock_code=code,
                        exchange_code="NSE",
                        product_type="cash",
                    ),
                    timeout_sec=10.0,
                )
                rows = raw_res.get("Success", []) if isinstance(raw_res, dict) else []
                if rows and isinstance(rows, list):
                    last_row = rows[-1]
                    lp = float(last_row.get("close", 0.0))
                    op = float(rows[0].get("open", lp))
                    chg = round(((lp - op) / op) * 100, 2) if op > 0 else 0.0
                    quote = Quote(
                        instrument_id=inst_id,
                        symbol=symbol,
                        last_price=lp,
                        open=op,
                        high=max(float(r.get("high", lp)) for r in rows[-75:]),
                        low=min(float(r.get("low", lp)) for r in rows[-75:]),
                        close=lp,
                        volume=sum(int(r.get("volume", 0)) for r in rows[-75:]),
                        change_pct=chg,
                        timestamp=now,
                    )
                    await self.ingest_quote(quote)
                    logger.info("Synced real Breeze quote for %s: LTP=%.2f", symbol, lp)
        except Exception as exc:
            logger.warning("Breeze quote sync deferred: %s", exc)

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

