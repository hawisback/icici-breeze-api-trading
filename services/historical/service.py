"""Historical Service providing candle retrieval, backfill, and synthetic data generation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import math
from time import monotonic
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
        instrument_service: Optional[Any] = None,
    ) -> None:
        self.repo = repository or HistoricalRepository()
        self.broker_gateway = broker_gateway
        self.instrument_service = instrument_service
        self._provider_retry_after: dict[tuple[str, str, str], float] = {}

    def set_broker_gateway(self, broker_gateway: Any) -> None:
        """Inject broker gateway for live candle retrieval."""
        self.broker_gateway = broker_gateway

    async def initialize(self) -> None:
        await self.repo.initialize()

    def _active_provider(self) -> tuple[str, Any | None]:
        """Return the provider configured for frequent candle refreshes."""
        if not self.broker_gateway:
            return "", None
        adapter = getattr(
            self.broker_gateway,
            "frequent_data_adapter",
            None,
        )
        name = str(
            getattr(
                self.broker_gateway,
                "frequent_data_broker_name",
                "",
            )
            or ""
        ).lower()
        if name not in {"breeze", "kite"}:
            name = str(
                getattr(self.broker_gateway, "active_broker_name", "")
                or ""
            ).lower()
            adapter = getattr(
                self.broker_gateway,
                "active_adapter",
                adapter,
            )
        if name not in {"breeze", "kite"}:
            breeze = getattr(self.broker_gateway, "breeze_adapter", None)
            client = getattr(breeze, "client_manager", None)
            if client and getattr(client, "is_active", False):
                return "breeze", breeze
            kite = getattr(self.broker_gateway, "kite_adapter", None)
            if kite and getattr(kite, "is_active", False):
                return "kite", kite
        return name, adapter

    def _provider_is_active(self) -> bool:
        name, adapter = self._active_provider()
        if name == "kite":
            return bool(adapter and getattr(adapter, "is_active", False))
        if name == "breeze":
            client = getattr(getattr(self.broker_gateway, "breeze_adapter", None), "client_manager", None)
            return bool(client and getattr(client, "is_active", False))
        return False

    @staticmethod
    def _expected_completed_end(interval: str, now: datetime) -> Optional[datetime]:
        ist = timezone(timedelta(hours=5, minutes=30))
        local = now.astimezone(ist)
        step = 15 if interval == "15m" else 5 if interval == "5m" else 1
        session_open = local.replace(hour=9, minute=15, second=0, microsecond=0)
        session_close = local.replace(hour=15, minute=30, second=0, microsecond=0)
        if local.weekday() >= 5 or local < session_open:
            day = local.date() - timedelta(days=1)
            while day.weekday() >= 5:
                day -= timedelta(days=1)
            return datetime.combine(day, datetime.min.time(), tzinfo=ist).replace(hour=15, minute=30).astimezone(timezone.utc)
        if local >= session_close:
            return session_close.astimezone(timezone.utc)
        elapsed = int((local - session_open).total_seconds() // 60)
        boundary = session_open + timedelta(minutes=(elapsed // step) * step)
        if (local - boundary).total_seconds() <= 120:
            boundary -= timedelta(minutes=step)
        return boundary.astimezone(timezone.utc) if boundary >= session_open else None

    @classmethod
    def expected_completed_end(
        cls,
        interval: str,
        now: datetime,
    ) -> Optional[datetime]:
        """Return the latest provider-safe candle end expected at the given time."""
        return cls._expected_completed_end(interval, now)

    async def fetch_candles_from_active_provider(self, instrument_id: str, interval: str = "5m", days_back: int = 5) -> list[Candle]:
        name, adapter = self._active_provider()
        retry_key = (name, instrument_id, interval)
        if monotonic() < self._provider_retry_after.get(retry_key, 0.0):
            return []
        if name == "kite":
            if not adapter or not getattr(adapter, "is_active", False):
                return []
            fetch = getattr(adapter, "fetch_historical_candles", None)
            if not callable(fetch):
                return []
            try:
                candles = await fetch(instrument_id=instrument_id, interval=interval, days_back=days_back)
                self._provider_retry_after.pop(retry_key, None) if candles else self._provider_retry_after.__setitem__(retry_key, monotonic() + 30.0)
                return candles
            except Exception as exc:
                logger.warning("Kite historical fetch failed for %s: %s", instrument_id, exc)
                self._provider_retry_after[retry_key] = monotonic() + 30.0
                return []
        if name == "breeze":
            candles = await self.fetch_candles_from_breeze(instrument_id, interval, days_back)
            self._provider_retry_after.pop(retry_key, None) if candles else self._provider_retry_after.__setitem__(retry_key, monotonic() + 30.0)
            return candles
        return []

    async def fetch_candles_from_provider_window(
        self,
        instrument_id: str,
        *,
        interval: str,
        start_time: datetime,
        end_time: datetime,
        requested_source: str,
    ) -> list[Candle]:
        """Fetch an exact historical window for deterministic replay.

        Replay must never translate a historical session into "days back from
        now".  The requested source is explicit so Breeze and Kite remain
        isolated even when both sessions have existed in the same database.
        """
        source = str(requested_source or "").upper()
        if source == "MIXED":
            active_name, _ = self._active_provider()
            source = active_name.upper()
        if source == "BREEZE":
            candles = await self.fetch_candles_from_breeze_window(
                instrument_id,
                interval=interval,
                start_time=start_time,
                end_time=end_time,
            )
        elif source == "KITE":
            gateway = self.broker_gateway
            adapter = getattr(gateway, "kite_adapter", None) if gateway else None
            if adapter is None and gateway and str(getattr(gateway, "active_broker_name", "")).lower() == "kite":
                adapter = getattr(gateway, "active_adapter", None)
            fetch = getattr(adapter, "fetch_historical_candles_window", None)
            if not adapter or not getattr(adapter, "is_active", False) or not callable(fetch):
                return []
            try:
                candles = await fetch(
                    instrument_id=instrument_id,
                    interval=interval,
                    start_time=start_time,
                    end_time=end_time,
                )
            except Exception as exc:
                logger.warning("Kite targeted historical fetch failed for %s: %s", instrument_id, exc)
                return []
            if candles:
                await self.repo.save_candles(candles)
        else:
            return []
        return sorted(candles, key=lambda candle: candle.start_time)

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

    async def _breeze_contract_args(self, instrument_id: str) -> dict[str, str]:
        """Build the contract-specific arguments required by Breeze V2.

        Breeze's historical endpoint does not infer an option from the
        platform instrument id.  Options must be requested using the
        underlying stock code plus NFO/options, expiry, right, and strike.
        Keeping this mapping here also makes the legacy historical service
        path behave the same as the clean broker adapter path.
        """
        if not self.instrument_service:
            return {}
        instrument = await self.instrument_service.get_instrument(instrument_id)
        if not instrument:
            return {}
        segment = str(instrument.segment or "").upper()
        if segment == "OPTIONS":
            if not instrument.expiry or instrument.strike is None or not instrument.option_right:
                return {}
            right = getattr(instrument.option_right, "value", instrument.option_right)
            return {
                "stock_code": instrument.underlying,
                "exchange_code": instrument.exchange or "NFO",
                "product_type": "options",
                "expiry_date": f"{instrument.expiry}T06:00:00.000Z",
                "right": "call" if str(right).upper() in {"CALL", "CE"} else "put",
                "strike_price": str(instrument.strike),
            }
        if segment == "FUTURES":
            if not instrument.expiry:
                return {}
            return {
                "stock_code": instrument.underlying,
                "exchange_code": instrument.exchange or "NFO",
                "product_type": "futures",
                "expiry_date": f"{instrument.expiry}T07:00:00.000Z",
                "right": "others",
                "strike_price": "0",
            }
        return {}

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
        contract_args = await self._breeze_contract_args(instrument_id)
        if contract_args:
            stock_code = contract_args.pop("stock_code")
            exchange = contract_args.pop("exchange_code")
            product_type = contract_args.pop("product_type")
        elif self.instrument_service:
            instrument = await self.instrument_service.get_instrument(instrument_id)
            if instrument and str(instrument.segment).upper() in {"OPTIONS", "FUTURES"}:
                logger.warning("Missing Breeze contract metadata for %s", instrument_id)
                return []
        breeze_interval, step_min = self._map_interval_to_breeze(interval)

        now = utc_now()
        # Breeze's historical API uses exchange (IST) wall-clock values in
        # ISO-shaped strings. Sending UTC dates/times shifts the requested
        # session and is especially visible on intraday charts.
        ist_tz = timezone(timedelta(hours=5, minutes=30))
        now_ist = now.astimezone(ist_tz)
        from_day = (now_ist - timedelta(days=days_back)).date()
        from_dt = datetime.combine(from_day, datetime.min.time(), tzinfo=ist_tz).replace(
            hour=9, minute=15
        ).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        to_dt = now_ist.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        try:
            rate_limiter = getattr(breeze_adapter, "rate_limiter", None)
            if rate_limiter is not None and hasattr(rate_limiter, "acquire_read"):
                await rate_limiter.acquire_read()
            sdk = client_mgr.get_sdk_client()
            logger.info(
                "Fetching real historical candles from Breeze: instrument=%s stock=%s exch=%s "
                "product=%s interval=%s contract=%s range=[%s to %s]",
                instrument_id,
                stock_code,
                exchange,
                product_type,
                breeze_interval,
                contract_args,
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
                    **contract_args,
                ),
                timeout_sec=15.0,
            )

            rows = raw_res.get("Success", []) if isinstance(raw_res, dict) else []
            if not rows or not isinstance(rows, list):
                logger.warning(
                    "Breeze returned no historical candles for instrument=%s stock=%s exchange=%s "
                    "product=%s contract=%s response=%s",
                    instrument_id, stock_code, exchange, product_type, contract_args, raw_res,
                )
                return []

            candles: list[Candle] = []

            for row in rows:
                dt_str = row.get("datetime")
                if not dt_str:
                    continue
                try:
                    try:
                        parsed = datetime.fromisoformat(str(dt_str).replace("Z", "+00:00"))
                    except ValueError:
                        parsed = datetime.strptime(str(dt_str), "%Y-%m-%d %H:%M:%S")
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=ist_tz)
                    c_start = parsed.astimezone(timezone.utc)
                    c_end = c_start + timedelta(minutes=5 if interval == "15m" else step_min)
                    volume = int(float(row.get("volume") or row.get("total_quantity_traded") or 0))
                    open_interest = int(float(row.get("open_interest") or row.get("oi") or 0))
                    candle = Candle(
                        instrument_id=instrument_id,
                        interval="5m" if interval == "15m" else interval,
                        start_time=c_start,
                        end_time=c_end,
                        open=float(row.get("open") or 0.0),
                        high=float(row.get("high") or 0.0),
                        low=float(row.get("low") or 0.0),
                        close=float(row.get("close") or 0.0),
                        volume=volume,
                        open_interest=open_interest,
                        source="BREEZE",
                    )
                    if candle.low <= min(candle.open, candle.close) <= max(candle.open, candle.close) <= candle.high and candle.low > 0:
                        candles.append(candle)
                    else:
                        logger.warning("Skipping invalid Breeze candle for %s at %s: %s", instrument_id, dt_str, row)
                except (TypeError, ValueError) as row_exc:
                    logger.warning("Skipping malformed Breeze candle for %s at %s: %s", instrument_id, dt_str, row_exc)

            if interval == "15m":
                candles = self._resample_to_15m(candles, instrument_id)

            logger.info("Successfully fetched %d real candles from Breeze for %s", len(candles), instrument_id)
            return candles
        except Exception as exc:
            logger.warning("Failed to fetch Breeze historical candles for %s: %s", instrument_id, exc)
            return []

    async def fetch_candles_from_breeze_window(
        self,
        instrument_id: str,
        *,
        interval: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[Candle]:
        """Fetch and persist one targeted Breeze historical window.

        This method is intended for replay-resolution data, not live polling.
        The caller supplies the already-identified ambiguous window; no
        strategy features or signals are calculated here.  Breeze expects the
        exchange-session wall-clock values in its ISO-shaped arguments, while
        returned timestamps are normalized to UTC before persistence.
        """
        if interval not in {"1m", "5m"}:
            raise ValueError("targeted Breeze windows support only 1m and 5m intervals")
        if end_time < start_time:
            raise ValueError("end_time must not precede start_time")
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
        ist_tz = timezone(timedelta(hours=5, minutes=30))

        start_ist = start_time.astimezone(ist_tz)
        end_ist = end_time.astimezone(ist_tz)
        from_dt = start_ist.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        to_dt = end_ist.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        contract_args = await self._breeze_contract_args(instrument_id)
        if contract_args:
            stock_code = contract_args.pop("stock_code")
            exchange = contract_args.pop("exchange_code")
            product_type = contract_args.pop("product_type")
        elif self.instrument_service:
            instrument = await self.instrument_service.get_instrument(instrument_id)
            if instrument and str(instrument.segment).upper() in {"OPTIONS", "FUTURES"}:
                logger.warning("Missing Breeze contract metadata for %s", instrument_id)
                return []

        try:
            rate_limiter = getattr(breeze_adapter, "rate_limiter", None)
            if rate_limiter is not None and hasattr(rate_limiter, "acquire_read"):
                await rate_limiter.acquire_read()
            sdk = client_mgr.get_sdk_client()
            raw_res = await client_mgr.sdk_runner.run(
                lambda: sdk.get_historical_data_v2(
                    interval=breeze_interval,
                    from_date=from_dt,
                    to_date=to_dt,
                    stock_code=stock_code,
                    exchange_code=exchange,
                    product_type=product_type,
                    **contract_args,
                ),
                timeout_sec=15.0,
            )
            rows = raw_res.get("Success", []) if isinstance(raw_res, dict) else []
            if not isinstance(rows, list):
                return []

            candles: list[Candle] = []
            for row in rows:
                dt_str = row.get("datetime")
                if not dt_str:
                    continue
                try:
                    parsed = datetime.fromisoformat(str(dt_str).replace("Z", "+00:00"))
                except ValueError:
                    parsed = datetime.strptime(str(dt_str), "%Y-%m-%d %H:%M:%S")
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=ist_tz)
                c_start = parsed.astimezone(timezone.utc)
                candles.append(
                    Candle(
                        instrument_id=instrument_id,
                        interval=interval,
                        start_time=c_start,
                        end_time=c_start + timedelta(minutes=step_min),
                        open=float(row.get("open", 0.0)),
                        high=float(row.get("high", 0.0)),
                        low=float(row.get("low", 0.0)),
                        close=float(row.get("close", 0.0)),
                        volume=int(float(row.get("volume", 0) or 0)),
                        open_interest=int(float(row.get("open_interest", 0) or 0)),
                        source="BREEZE",
                    )
                )

            candles = sorted(
                {
                    candle.start_time: candle
                    for candle in candles
                    if start_time <= candle.start_time <= end_time
                }.values(),
                key=lambda candle: candle.start_time,
            )
            if candles:
                await self.repo.save_candles(candles)
            return candles
        except Exception as exc:
            logger.warning(
                "Failed to fetch targeted Breeze window for %s [%s, %s]: %s",
                instrument_id,
                from_dt,
                to_dt,
                exc,
            )
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
            b_candles.sort(key=lambda c: c.start_time)
            if len(b_candles) != 3 or any(c.start_time != b_start + timedelta(minutes=5*i) for i, c in enumerate(b_candles)):
                continue
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
        requested_source: Optional[str] = None,
        allow_provider_fallback: bool = True,
        allow_synthetic_fallback: bool = True,
    ) -> list[Candle]:
        """Return historical candles.

        ``requested_source`` and the fallback flags are replay-boundary
        controls.  Existing callers retain the prior live/chart behavior by
        using the defaults; deterministic replay disables provider and
        synthetic fallback explicitly.
        """
        provider_name, _ = self._active_provider()
        provider_active = self._provider_is_active()
        expected_source = "KITE" if provider_name == "kite" else "BREEZE" if provider_name == "breeze" else None
        source_allows_provider = requested_source in (None, "MIXED", expected_source)
        latest_candle = await self.repo.get_latest_candle(instrument_id, interval)
        latest_matches = bool(latest_candle and expected_source and latest_candle.source == expected_source)
        attempted = False

        expected_end = self._expected_completed_end(interval, utc_now())
        if provider_active and expected_source and source_allows_provider and (
            not latest_matches or (expected_end is not None and latest_candle.end_time < expected_end)
        ):
            attempted = True
            fetched = await self.fetch_candles_from_active_provider(
                instrument_id,
                interval,
            )
            if fetched and expected_end is not None:
                fetched = [
                    candle
                    for candle in fetched
                    if candle.end_time <= expected_end
                ]
            if fetched:
                await self.repo.purge_simulated_candles(instrument_id, interval)
                await self.repo.save_candles(fetched)

        candles = await self.repo.get_candles(
            instrument_id=instrument_id, interval=interval, start_time=start_time,
            end_time=end_time, limit=limit,
        )
        if requested_source in ("BREEZE", "KITE"):
            candles = [x for x in candles if x.source == requested_source]
        elif expected_source and requested_source in (None, "MIXED"):
            # Provider identity is a configuration boundary, not merely a
            # connectivity hint. Never serve cached Breeze candles to a Kite
            # runtime (or vice versa), even while the selected broker session
            # is temporarily inactive.
            candles = [x for x in candles if x.source == expected_source]

        if not candles and provider_active and expected_source and source_allows_provider and allow_provider_fallback and not attempted:
            fetched = await self.fetch_candles_from_active_provider(
                instrument_id,
                interval,
            )
            if fetched and expected_end is not None:
                fetched = [
                    candle
                    for candle in fetched
                    if candle.end_time <= expected_end
                ]
            if fetched:
                await self.repo.purge_simulated_candles(instrument_id, interval)
                await self.repo.save_candles(fetched)
                return sorted(fetched, key=lambda x: x.start_time)[-limit:]

        if not candles and allow_synthetic_fallback:
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
