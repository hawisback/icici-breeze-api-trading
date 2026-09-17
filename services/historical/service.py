"""Historical Service providing candle retrieval, backfill, and synthetic data generation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import math
from typing import Any, Optional

from libs.contracts.models import Candle, utc_now
from services.historical.repository import HistoricalRepository

logger = logging.getLogger(__name__)


class HistoricalService:
    """Provides historical candles, gap detection, and backfilling."""

    def __init__(
        self,
        repository: Optional[HistoricalRepository] = None,
        broker_gateway: Optional[Any] = None,
    ) -> None:
        self.repo = repository or HistoricalRepository()
        self.broker_gateway = broker_gateway

    def set_broker_gateway(self, broker_gateway: Any) -> None:
        """Inject broker gateway for live candle retrieval."""
        self.broker_gateway = broker_gateway

    async def initialize(self) -> None:
        await self.repo.initialize()

    def _map_instrument_to_breeze(self, instrument_id: str) -> tuple[str, str, str]:
        """Map platform instrument ID to Breeze stock_code, exchange_code, and product_type."""
        inst_upper = instrument_id.upper()
        if "BANKNIFTY" in inst_upper or inst_upper in ("CNXBAN", "NIFTY BANK"):
            return "CNXBAN", "NSE", "cash"
        if "NIFTY" in inst_upper:
            return "NIFTY", "NSE", "cash"
        if "RELIANCE" in inst_upper or "RELIND" in inst_upper:
            return "RELIND", "NSE", "cash"
        if "TCS" in inst_upper:
            return "TCS", "NSE", "cash"
        if "INFY" in inst_upper:
            return "INFY", "NSE", "cash"
        return instrument_id, "NSE", "cash"

    def _map_interval_to_breeze(self, interval: str) -> tuple[str, int]:
        """Map standard interval to Breeze interval string and duration in minutes."""
        if interval == "1m":
            return "1minute", 1
        if interval == "5m":
            return "5minute", 5
        if interval == "15m":
            return "5minute", 15
        if interval == "30m":
            return "30minute", 30
        if interval in ("1D", "1d", "D"):
            return "1day", 1440
        return "5minute", 5

    async def fetch_candles_from_breeze(
        self,
        instrument_id: str,
        interval: str = "5m",
        days_back: int = 5,
    ) -> list[Candle]:
        """Fetch official historical candle series directly from ICICI Direct Breeze API."""
        if not self.broker_gateway:
            return []

        breeze_adapter = getattr(self.broker_gateway, "breeze_adapter", None)
        if not breeze_adapter or not hasattr(breeze_adapter, "client_manager"):
            return []

        client_mgr = breeze_adapter.client_manager
        if not client_mgr.is_active:
            return []

        stock_code, exchange, product_type = self._map_instrument_to_breeze(instrument_id)
        breeze_interval, step_min = self._map_interval_to_breeze(interval)

        now = utc_now()
        from_dt = (now - timedelta(days=days_back)).strftime("%Y-%m-%dT09:15:00.000Z")
        to_dt = now.strftime("%Y-%m-%dT15:30:00.000Z")

        try:
            sdk = client_mgr.get_sdk_client()
            logger.info(
                "Fetching real historical candles from Breeze: stock=%s exch=%s interval=%s range=[%s to %s]",
                stock_code,
                exchange,
                breeze_interval,
                from_dt,
                to_dt,
            )
            raw_res = await client_mgr.sdk_runner.run(
                lambda: sdk.get_historical_data_v2(
                    interval=breeze_interval,
                    from_date=from_dt,
                    to_date=to_dt,
                    stock_code=stock_code,
                    exchange_code=exchange,
                    product_type=product_type,
                ),
                timeout_sec=15.0,
            )

            rows = raw_res.get("Success", []) if isinstance(raw_res, dict) else []
            if not rows or not isinstance(rows, list):
                logger.info("Breeze returned empty candle list for %s: %s", stock_code, raw_res)
                return []

            ist_tz = timezone(timedelta(hours=5, minutes=30))
            candles: list[Candle] = []

            for row in rows:
                dt_str = row.get("datetime")
                if not dt_str:
                    continue

                # Parse Breeze IST datetime string
                dt_ist = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ist_tz)
                c_start = dt_ist.astimezone(timezone.utc)
                c_end = c_start + timedelta(minutes=step_min)

                candles.append(
                    Candle(
                        instrument_id=instrument_id,
                        interval=interval,
                        start_time=c_start,
                        end_time=c_end,
                        open=float(row.get("open", 0.0)),
                        high=float(row.get("high", 0.0)),
                        low=float(row.get("low", 0.0)),
                        close=float(row.get("close", 0.0)),
                        volume=int(row.get("volume", 0)),
                        open_interest=int(row.get("open_interest") or 0),
                        source="BREEZE",
                    )
                )

            if interval == "15m":
                candles = self._resample_to_15m(candles, instrument_id)

            logger.info("Successfully fetched %d real candles from Breeze for %s", len(candles), instrument_id)
            return candles
        except Exception as exc:
            logger.warning("Failed to fetch Breeze historical candles for %s: %s", instrument_id, exc)
            return []

    def _resample_to_15m(self, candles_5m: list[Candle], instrument_id: str) -> list[Candle]:
        """Aggregate 5-minute candles into standard 15-minute bars."""
        if not candles_5m:
            return []
        res: list[Candle] = []
        buckets: dict[datetime, list[Candle]] = {}
        for c in candles_5m:
            minute = (c.start_time.minute // 15) * 15
            bucket_dt = c.start_time.replace(minute=minute, second=0, microsecond=0)
            buckets.setdefault(bucket_dt, []).append(c)

        for b_start, b_candles in sorted(buckets.items()):
            b_end = b_start + timedelta(minutes=15)
            res.append(
                Candle(
                    instrument_id=instrument_id,
                    interval="15m",
                    start_time=b_start,
                    end_time=b_end,
                    open=b_candles[0].open,
                    high=max(x.high for x in b_candles),
                    low=min(x.low for x in b_candles),
                    close=b_candles[-1].close,
                    volume=sum(x.volume for x in b_candles),
                    open_interest=b_candles[-1].open_interest,
                    source="BREEZE",
                )
            )
        return res

    async def get_candles(
        self,
        instrument_id: str,
        interval: str = "5m",
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 500,
    ) -> list[Candle]:
        breeze_active = False
        if self.broker_gateway:
            breeze_adapter = getattr(self.broker_gateway, "breeze_adapter", None)
            if breeze_adapter and hasattr(breeze_adapter, "client_manager"):
                breeze_active = getattr(breeze_adapter.client_manager, "is_active", False)

        latest_candle = await self.repo.get_latest_candle(instrument_id, interval)

        # Proactively fetch from Breeze if session is active and cached candles are missing or simulated
        if breeze_active and (not latest_candle or latest_candle.source == "SIMULATED"):
            breeze_candles = await self.fetch_candles_from_breeze(instrument_id, interval)
            if breeze_candles:
                await self.repo.purge_simulated_candles(instrument_id, interval)
                await self.repo.save_candles(breeze_candles)

        candles = await self.repo.get_candles(
            instrument_id=instrument_id,
            interval=interval,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )

        has_only_simulated = bool(candles and all(c.source == "SIMULATED" for c in candles))
        if (not candles or has_only_simulated) and breeze_active:
            breeze_candles = await self.fetch_candles_from_breeze(instrument_id, interval)
            if breeze_candles:
                await self.repo.purge_simulated_candles(instrument_id, interval)
                await self.repo.save_candles(breeze_candles)
                return breeze_candles[-limit:]

        if not candles:
            # Fall back to realistic synthetic candles if offline
            candles = await self.generate_synthetic_candles(instrument_id, interval, count=min(limit, 100))
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

