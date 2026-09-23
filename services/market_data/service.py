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
                source="SIMULATED",
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
                source="SIMULATED",
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

    @staticmethod
    def _market_session_open(now: Optional[datetime] = None) -> bool:
        """Return whether the regular NSE cash/derivatives session is open."""
        now = now or utc_now()
        ist = timezone(timedelta(hours=5, minutes=30))
        local = now.astimezone(ist)
        if local.weekday() >= 5:
            return False
        minutes = local.hour * 60 + local.minute
        return 9 * 60 + 15 <= minutes <= 15 * 60 + 30

    def _live_broker_active(self) -> bool:
        """Return whether any configured live-data provider is active."""
        if not self.broker_gateway:
            return False

        order = getattr(self.broker_gateway, "provider_order", None)
        resolver = getattr(self.broker_gateway, "get_broker_adapter", None)
        if callable(order) and callable(resolver):
            for provider in order("live"):
                try:
                    if self.broker_gateway.provider_is_active(provider):
                        return True
                except Exception:
                    continue
            return False

        # Backward-compatible gateway/test-double shape.
        provider = str(getattr(self.broker_gateway, "active_broker_name", "") or "").lower()
        active_adapter = getattr(self.broker_gateway, "active_adapter", None)
        breeze_adapter = getattr(self.broker_gateway, "breeze_adapter", None)
        client_mgr = getattr(breeze_adapter, "client_manager", None)
        if provider == "kite":
            return bool(active_adapter and getattr(active_adapter, "is_active", False))
        if provider == "breeze":
            return bool(client_mgr and getattr(client_mgr, "is_active", False))
        if active_adapter and getattr(active_adapter, "is_active", False):
            return True
        return bool(client_mgr and getattr(client_mgr, "is_active", False))

    async def sync_quotes_from_broker(self) -> bool:
        """Fetch index quotes from the configured primary provider with fallback."""
        if not self.broker_gateway:
            return False

        order = getattr(self.broker_gateway, "provider_order", None)
        resolver = getattr(self.broker_gateway, "get_broker_adapter", None)
        if callable(order) and callable(resolver):
            candidates = []
            for provider in order("live"):
                try:
                    candidates.append((provider, resolver(provider)))
                except Exception:
                    continue
        else:
            provider = str(getattr(self.broker_gateway, "active_broker_name", "") or "").lower()
            if provider == "kite":
                candidates = [("kite", getattr(self.broker_gateway, "active_adapter", None))]
            else:
                candidates = [("breeze", getattr(self.broker_gateway, "breeze_adapter", None))]

        for provider, adapter in candidates:
            if adapter is None:
                continue
            active = bool(getattr(adapter, "is_active", False))
            client_mgr = getattr(adapter, "client_manager", None)
            active = active or bool(client_mgr and getattr(client_mgr, "is_active", False))
            if not active:
                continue
            fetch = getattr(adapter, "get_index_quotes", None)
            if callable(fetch):
                try:
                    quotes = await fetch()
                    if quotes:
                        for quote in quotes:
                            await self.ingest_quote(quote)
                        return True
                except Exception as exc:
                    logger.warning("%s live quote sync deferred: %s", provider, exc)
                    continue

            # Legacy Breeze adapter/test-double path only.
            if client_mgr is not None:
                try:
                    sdk = client_mgr.get_sdk_client()
                    now = utc_now()
                    synced_any = False
                    for inst_id, symbol, code in (
                        ("INST-NIFTY-INDEX", "NIFTY 50", "NIFTY"),
                        ("INST-BANKNIFTY-INDEX", "NIFTY BANK", "CNXBAN"),
                    ):
                        raw = await client_mgr.sdk_runner.run(
                            lambda c=code: sdk.get_quotes(
                                stock_code=c,
                                exchange_code="NSE",
                                product_type="cash",
                            ),
                            timeout_sec=5.0,
                        )
                        rows = raw.get("Success", []) if isinstance(raw, dict) else []
                        if rows and isinstance(rows, list):
                            row = rows[0]
                            last = float(row.get("ltp") or 0.0)
                            if last > 0:
                                await self.ingest_quote(
                                    Quote(
                                        source="BREEZE",
                                        instrument_id=inst_id,
                                        symbol=symbol,
                                        last_price=last,
                                        open=float(row.get("open") or last),
                                        high=float(row.get("high") or last),
                                        low=float(row.get("low") or last),
                                        close=last,
                                        volume=int(row.get("total_quantity_traded") or 0),
                                        change_pct=float(row.get("ltp_percent_change") or 0.0),
                                        timestamp=now,
                                    )
                                )
                                synced_any = True
                    if synced_any:
                        return True
                except Exception as exc:
                    logger.warning("Legacy Breeze live quote sync deferred: %s", exc)
        return False

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

    async def start_feed_loop(self, interval_sec: float = 2.5) -> None:
        """Continuous live market data feed loop updating quotes and candles."""
        if self._running:
            return
        self._running = True
        self._simulation_task = asyncio.create_task(self._run_feed_loop(interval_sec))
        logger.info("Market data continuous feed worker started (interval=%.1fs).", interval_sec)

    async def stop_feed_loop(self) -> None:
        self._running = False
        if self._simulation_task:
            self._simulation_task.cancel()
            try:
                await self._simulation_task
            except asyncio.CancelledError:
                pass
            self._simulation_task = None
        logger.info("Market data feed worker stopped.")

    async def start_simulated_feed(self, interval_sec: float = 1.0) -> None:
        await self.start_feed_loop(interval_sec=interval_sec)

    async def stop_simulated_feed(self) -> None:
        await self.stop_feed_loop()

    async def _run_feed_loop(self, interval_sec: float) -> None:
        while self._running:
            try:
                # REST polling is limited to the regular market session. The
                # primary provider is tried first and the secondary provider is
                # only used on failure/unavailability.
                synced = False
                live_broker_active = self._live_broker_active()
                if live_broker_active and self._market_session_open():
                    synced = await self.sync_quotes_from_broker()
                elif live_broker_active:
                    synced = True

                # 2. Synthetic ticks are an offline-only aid.  Never overwrite
                # a failed/stale live broker read with a plausible fake quote:
                # that previously made the UI show LIVE while pinning NIFTY to
                # the old seeded close and corrupted the forming chart bar.
                if not synced and not live_broker_active:
                    for inst_id in ["INST-NIFTY-INDEX", "INST-BANKNIFTY-INDEX"]:
                        q = self.get_latest_quote(inst_id)
                        if not q:
                            continue
                        delta = (random.random() - 0.49) * 2.0
                        new_price = round(q.last_price + delta, 2)
                        new_quote = Quote(
                            source="SIMULATED",
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
                logger.error("Error in market feed loop: %s", e)
                await asyncio.sleep(interval_sec)
