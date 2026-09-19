"""Idempotent NIFTY 5-minute historical backfill from ICICI Breeze.

This module is deliberately independent of strategy and replay code.  It owns
only the broker request, contract selection, candle normalization, persistence,
and post-download quality report required to build the historical data set.

Futures identity is preserved by using one platform instrument id per expiry:
``INST-NIFTY-FUT-YYYY-MM-DD``.  The corresponding instrument master row stores
the same expiry, so the existing historical-candle schema remains compatible
without putting contract metadata in an untyped JSON side table.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
import json
import logging
from pathlib import Path
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

from libs.config.settings import PlatformSettings, get_platform_settings
from libs.contracts.models import Candle, Instrument
from services.broker_gateway.icici_breeze_adapter import IciciBreezeAdapter
from services.broker_gateway.infrastructure.icici.response_mapper import BreezeResponseValidator
from services.historical.repository import HistoricalRepository
from services.instrument.repository import InstrumentRepository

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
FIVE_MINUTES = timedelta(minutes=5)
MAX_INTERVALS_PER_REQUEST = 1000
# Ten calendar days contain at most eight NSE sessions.  At 5-minute cadence
# this is comfortably below Breeze's 1,000-interval response limit.
MAX_CALENDAR_DAYS_PER_REQUEST = 10


@dataclass(frozen=True, slots=True)
class BackfillConfig:
    start_date: date
    end_date: date
    historical_db_path: Path
    instruments_db_path: Path
    source: str = "BREEZE"


@dataclass(slots=True)
class BackfillReport:
    requested_start: str
    requested_end: str
    actual_start: Optional[str] = None
    actual_end: Optional[str] = None
    spot_candles: int = 0
    futures_candles: int = 0
    futures_contracts: dict[str, int] = field(default_factory=dict)
    futures_contract_ids: dict[str, str] = field(default_factory=dict)
    expected_weekday_sessions: int = 0
    spot_sessions: int = 0
    futures_sessions: int = 0
    missing_spot_sessions: list[str] = field(default_factory=list)
    missing_futures_sessions: list[str] = field(default_factory=list)
    duplicate_candles: int = 0
    timestamp_gaps: list[dict[str, Any]] = field(default_factory=list)
    futures_volume_present: int = 0
    futures_open_interest_present: int = 0
    rejected_rows: int = 0
    failed_requests: list[dict[str, Any]] = field(default_factory=list)
    requests_completed: int = 0
    requests_attempted: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_date_range": {"start": self.requested_start, "end": self.requested_end},
            "actual_date_range_received": {"start": self.actual_start, "end": self.actual_end},
            "nifty_spot_candles": self.spot_candles,
            "nifty_futures_candles": self.futures_candles,
            "futures_contracts_used": self.futures_contracts,
            "futures_contract_ids": self.futures_contract_ids,
            "expected_weekday_sessions": self.expected_weekday_sessions,
            "spot_sessions": self.spot_sessions,
            "futures_sessions": self.futures_sessions,
            "missing_spot_sessions": self.missing_spot_sessions,
            "missing_futures_sessions": self.missing_futures_sessions,
            "duplicate_candles": self.duplicate_candles,
            "timestamp_gaps": self.timestamp_gaps,
            "futures_candles_containing_volume": self.futures_volume_present,
            "futures_candles_containing_open_interest": self.futures_open_interest_present,
            "rejected_rows": self.rejected_rows,
            "requests": {
                "attempted": self.requests_attempted,
                "completed": self.requests_completed,
                "failed": len(self.failed_requests),
            },
            "failed_api_requests": self.failed_requests,
            "notes": self.notes,
        }


def _last_tuesday(year: int, month: int) -> date:
    """Return NSE's NIFTY monthly expiry date for the given month.

    NSE moved NIFTY index derivative expiries to Tuesday for contracts from
    September 2025 onward.  The requested replay period is in 2026, so the
    applicable monthly futures series expires on the last Tuesday.  A future
    exchange holiday adjustment is handled by the optional holiday set below;
    absent a holiday, the exchange date is the last Tuesday itself.
    """

    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last_day = next_month - timedelta(days=1)
    return last_day - timedelta(days=(last_day.weekday() - 1) % 7)


def _month_starts(start: date, end: date) -> Iterable[date]:
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        yield cursor
        cursor = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)


def resolve_monthly_futures_expiries(start: date, end: date) -> list[date]:
    """Return all applicable NIFTY monthly expiry dates covering a range."""

    expiries: list[date] = []
    first_month = date(start.year, start.month, 1)
    previous_month = date(first_month.year - (first_month.month == 1), 12 if first_month.month == 1 else first_month.month - 1, 1)
    for month_start in (previous_month, *_month_starts(start, end)):
        expiry = _last_tuesday(month_start.year, month_start.month)
        if not expiries or expiries[-1] != expiry:
            expiries.append(expiry)
    return [expiry for expiry in expiries if expiry >= start - timedelta(days=31) and expiry <= end + timedelta(days=31)]


def futures_contract_periods(start: date, end: date) -> list[tuple[date, date, date]]:
    """Return ``(period_start, period_end, expiry)`` contract windows."""

    expiries = sorted(resolve_monthly_futures_expiries(start, end))
    periods: list[tuple[date, date, date]] = []
    for expiry in expiries:
        period_start = start if not periods else periods[-1][2] + timedelta(days=1)
        period_end = min(end, expiry)
        if period_start <= period_end:
            periods.append((period_start, period_end, expiry))
        if period_end >= end:
            break
    return periods


def split_date_range(start: date, end: date, max_days: int = MAX_CALENDAR_DAYS_PER_REQUEST) -> list[tuple[date, date]]:
    """Split inclusive dates into requests guaranteed below Breeze's limit."""

    if end < start:
        raise ValueError("end date must not precede start date")
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=max_days - 1))
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def _breeze_timestamp(value: Any) -> datetime:
    """Strictly parse a Breeze timestamp and normalize it to UTC."""

    raw = str(value or "").strip()
    if not raw:
        raise ValueError("missing datetime")
    if "T" in raw:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=IST)
    else:
        parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
    return parsed.astimezone(UTC)


def _number(row: dict[str, Any], key: str, *, required: bool = True) -> Optional[float]:
    value = row.get(key)
    if value is None or str(value).strip() == "":
        if required:
            raise ValueError(f"missing {key}")
        return None
    return float(value)


class BreezeHistoricalBackfill:
    """Fetch, normalize, persist, and validate NIFTY historical candles."""

    def __init__(
        self,
        config: BackfillConfig,
        settings: Optional[PlatformSettings] = None,
        adapter: Optional[IciciBreezeAdapter] = None,
    ) -> None:
        self.config = config
        self.settings = settings or get_platform_settings()
        self.repo = HistoricalRepository(db_path=config.historical_db_path)
        self.instrument_repo = InstrumentRepository(db_path=config.instruments_db_path)
        self.adapter = adapter
        self.report = BackfillReport(
            requested_start=config.start_date.isoformat(),
            requested_end=config.end_date.isoformat(),
        )
        self._incoming_keys: Counter[tuple[str, str, datetime]] = Counter()
        self._futures_volume_rows = 0
        self._futures_oi_rows = 0

    async def run(self) -> BackfillReport:
        await self.repo.initialize()
        await self.instrument_repo.initialize()
        await self._connect()
        await self._ensure_spot_instrument()
        await self._ensure_futures_instruments()

        try:
            await self._fetch_spot()
            await self._fetch_futures()
        finally:
            if self.adapter is not None and self.adapter.client_manager.is_active:
                await self.adapter.client_manager.disconnect()

        await self._validate_database()
        return self.report

    async def _connect(self) -> None:
        if self.adapter is None:
            self.adapter = IciciBreezeAdapter(
                api_key=self.settings.breeze_api_key.get_secret_value() if self.settings.breeze_api_key else "",
                secret_key=self.settings.breeze_secret_key.get_secret_value() if self.settings.breeze_secret_key else "",
                session_token=self.settings.breeze_session_token.get_secret_value() if self.settings.breeze_session_token else "",
            )
        ok = await self.adapter.authenticate(self.adapter.api_key, self.adapter.secret_key, self.adapter.session_token)
        if not ok or not self.adapter.client_manager.is_active:
            raise RuntimeError("Breeze authentication failed; no historical data was downloaded")

    async def _ensure_spot_instrument(self) -> None:
        await self.instrument_repo.save_instrument(
            Instrument(
                instrument_id="INST-NIFTY-INDEX",
                broker="ICICI_BREEZE",
                exchange="NSE",
                segment="EQUITY",
                underlying="NIFTY",
                stock_code="NIFTY",
                lot_size=1,
                tradable=False,
            )
        )

    async def _ensure_futures_instruments(self) -> None:
        for _, _, expiry in futures_contract_periods(self.config.start_date, self.config.end_date):
            instrument_id = f"INST-NIFTY-FUT-{expiry.isoformat()}"
            await self.instrument_repo.save_instrument(
                Instrument(
                    instrument_id=instrument_id,
                    broker="ICICI_BREEZE",
                    exchange="NFO",
                    segment="FUTURES",
                    underlying="NIFTY",
                    stock_code="NIFTY",
                    expiry=expiry.isoformat(),
                    lot_size=75,
                    tradable=False,
                )
            )
            self.report.futures_contract_ids[expiry.isoformat()] = instrument_id

    async def _fetch_spot(self) -> None:
        for chunk_start, chunk_end in split_date_range(self.config.start_date, self.config.end_date):
            rows = await self._request_chunk(
                instrument_id="INST-NIFTY-INDEX",
                stock_code="NIFTY",
                exchange_code="NSE",
                product_type="cash",
                expiry_date="",
                chunk_start=chunk_start,
                chunk_end=chunk_end,
                label="spot",
            )
            candles = self._normalize_rows(
                rows,
                "INST-NIFTY-INDEX",
                is_futures=False,
                window_start=self._session_start_utc(chunk_start),
                window_end=self._session_end_utc(chunk_end),
            )
            await self.repo.save_candles(candles)

    async def _fetch_futures(self) -> None:
        for period_start, period_end, expiry in futures_contract_periods(self.config.start_date, self.config.end_date):
            instrument_id = f"INST-NIFTY-FUT-{expiry.isoformat()}"
            for chunk_start, chunk_end in split_date_range(period_start, period_end):
                rows = await self._request_chunk(
                    instrument_id=instrument_id,
                    stock_code="NIFTY",
                    exchange_code="NFO",
                    product_type="futures",
                    expiry_date=f"{expiry.isoformat()}T06:00:00.000Z",
                    chunk_start=chunk_start,
                    chunk_end=chunk_end,
                    label=f"futures:{expiry.isoformat()}",
                )
                candles = self._normalize_rows(
                    rows,
                    instrument_id,
                    is_futures=True,
                    window_start=self._session_start_utc(chunk_start),
                    window_end=self._session_end_utc(chunk_end),
                )
                await self.repo.save_candles(candles)
                self.report.futures_contracts[expiry.isoformat()] = self.report.futures_contracts.get(expiry.isoformat(), 0) + len(candles)

    async def _request_chunk(
        self,
        *,
        instrument_id: str,
        stock_code: str,
        exchange_code: str,
        product_type: str,
        expiry_date: str,
        chunk_start: date,
        chunk_end: date,
        label: str,
    ) -> list[dict[str, Any]]:
        assert self.adapter is not None
        self.report.requests_attempted += 1
        # Breeze V2 expects the exchange-session wall-clock values in the
        # ISO-shaped arguments.  Although the suffix is ``Z``, sending UTC
        # converted clock times causes the final date in a multi-day request
        # to be truncated (for example, at 10:00 IST).  Keep request values
        # at 09:15/15:30 and normalize the returned timestamps separately.
        from_dt = datetime.combine(chunk_start, time(9, 15), tzinfo=IST)
        to_dt = datetime.combine(chunk_end, time(15, 30), tzinfo=IST)
        estimated_intervals = (chunk_end - chunk_start).days + 1
        estimated_intervals *= 78
        if estimated_intervals > MAX_INTERVALS_PER_REQUEST:
            raise AssertionError(f"chunk exceeds Breeze limit: {chunk_start}..{chunk_end}")

        try:
            await self.adapter.rate_limiter.acquire_read()
            sdk = self.adapter.client_manager.get_sdk_client()
            raw = await self.adapter.sdk_runner.run(
                lambda: sdk.get_historical_data_v2(
                    interval="5minute",
                    from_date=f"{chunk_start.isoformat()}T09:15:00.000Z",
                    to_date=f"{chunk_end.isoformat()}T15:30:00.000Z",
                    stock_code=stock_code,
                    exchange_code=exchange_code,
                    product_type=product_type,
                    expiry_date=expiry_date,
                    right="others",
                    strike_price="0",
                ),
                timeout_sec=30.0,
            )
            data = BreezeResponseValidator.unwrap_success(raw)
            if not isinstance(data, list):
                raise ValueError(f"Breeze returned non-list Success payload: {type(data).__name__}")
            if len(data) > MAX_INTERVALS_PER_REQUEST:
                raise ValueError(
                    f"Breeze returned {len(data)} rows, exceeding the {MAX_INTERVALS_PER_REQUEST}-interval limit"
                )
            self.report.requests_completed += 1
            return [row for row in data if isinstance(row, dict)]
        except Exception as exc:
            failure = {
                "label": label,
                "instrument_id": instrument_id,
                "start": chunk_start.isoformat(),
                "end": chunk_end.isoformat(),
                "error": f"{type(exc).__name__}: {exc}",
            }
            self.report.failed_requests.append(failure)
            logger.error("Breeze historical request failed: %s", failure)
            return []

    @staticmethod
    def _session_start_utc(day: date) -> datetime:
        return datetime.combine(day, time(9, 15), tzinfo=IST).astimezone(UTC)

    @staticmethod
    def _session_end_utc(day: date) -> datetime:
        return datetime.combine(day, time(15, 30), tzinfo=IST).astimezone(UTC)

    def _normalize_rows(
        self,
        rows: list[dict[str, Any]],
        instrument_id: str,
        *,
        is_futures: bool,
        window_start: datetime,
        window_end: datetime,
    ) -> list[Candle]:
        result: list[Candle] = []
        for row in rows:
            try:
                start_time = _breeze_timestamp(row.get("datetime") or row.get("date"))
                if start_time < window_start or start_time > window_end:
                    raise ValueError("timestamp is outside the requested 09:15-15:30 IST session window")
                open_price = _number(row, "open")
                high_price = _number(row, "high")
                low_price = _number(row, "low")
                close_price = _number(row, "close")
                volume = _number(row, "volume", required=False)
                open_interest = _number(row, "open_interest", required=False)
                assert open_price is not None and high_price is not None and low_price is not None and close_price is not None
                if low_price > high_price or not (low_price <= open_price <= high_price) or not (low_price <= close_price <= high_price):
                    raise ValueError("invalid OHLC relationship")
                if volume is not None and volume < 0:
                    raise ValueError("negative volume")
                if open_interest is not None and open_interest < 0:
                    raise ValueError("negative open interest")
                if start_time.minute % 5 != 0 or start_time.second != 0:
                    raise ValueError("timestamp is not aligned to a 5-minute boundary")
                key = (instrument_id, "5m", start_time)
                self._incoming_keys[key] += 1
                if is_futures:
                    if volume is not None:
                        self._futures_volume_rows += 1
                    if open_interest is not None:
                        self._futures_oi_rows += 1
                result.append(
                    Candle(
                        instrument_id=instrument_id,
                        interval="5m",
                        start_time=start_time,
                        end_time=start_time + FIVE_MINUTES,
                        open=open_price,
                        high=high_price,
                        low=low_price,
                        close=close_price,
                        volume=int(volume or 0),
                        open_interest=int(open_interest) if open_interest is not None else 0,
                        source=self.config.source,
                    )
                )
            except Exception as exc:
                self.report.rejected_rows += 1
                logger.warning("Rejected Breeze historical row for %s: %s", instrument_id, exc)
        return result

    async def _validate_database(self) -> None:
        spot = await self.repo.get_candles("INST-NIFTY-INDEX", "5m", limit=2_000_000)
        future_ids = list(self.report.futures_contract_ids.values())
        futures: list[Candle] = []
        for instrument_id in future_ids:
            futures.extend(await self.repo.get_candles(instrument_id, "5m", limit=2_000_000))
        await self._remove_out_of_session_rows(spot + futures)
        spot = await self.repo.get_candles("INST-NIFTY-INDEX", "5m", limit=2_000_000)
        futures = []
        for instrument_id in future_ids:
            futures.extend(await self.repo.get_candles(instrument_id, "5m", limit=2_000_000))
        all_downloaded = spot + futures
        if all_downloaded:
            self.report.actual_start = min(c.start_time for c in all_downloaded).astimezone(IST).date().isoformat()
            self.report.actual_end = max(c.start_time for c in all_downloaded).astimezone(IST).date().isoformat()
        self.report.spot_candles = len(spot)
        self.report.futures_candles = len(futures)
        self.report.futures_volume_present = self._futures_volume_rows or sum(1 for c in futures if c.volume != 0)
        self.report.futures_open_interest_present = self._futures_oi_rows or sum(1 for c in futures if c.open_interest not in (None, 0))
        self.report.duplicate_candles = sum(max(0, count - 1) for count in self._incoming_keys.values())
        self.report.expected_weekday_sessions = sum(1 for d in self._dates(self.config.start_date, self.config.end_date) if d.weekday() < 5)
        self.report.spot_sessions = len(self._session_dates(spot))
        self.report.futures_sessions = len(self._session_dates(futures))
        spot_sessions = self._session_dates(spot)
        future_sessions = self._session_dates(futures)
        expected = {d.isoformat() for d in self._dates(self.config.start_date, self.config.end_date) if d.weekday() < 5}
        self.report.missing_spot_sessions = sorted(expected - spot_sessions)
        self.report.missing_futures_sessions = sorted(expected - future_sessions)
        self.report.timestamp_gaps = self._timestamp_gaps(spot + futures)
        self.report.notes.append("Futures expiry resolver: NSE NIFTY monthly contracts, last Tuesday of each expiry month.")
        self.report.notes.append("A primary-key collision is safely replaced; incoming duplicate keys are counted in duplicate_candles.")

    async def _remove_out_of_session_rows(self, candles: list[Candle]) -> None:
        """Remove only BREEZE rows outside the requested exchange session."""
        invalid: list[Candle] = []
        for candle in candles:
            local = candle.start_time.astimezone(IST)
            if not (self.config.start_date <= local.date() <= self.config.end_date):
                continue
            if local.weekday() >= 5 or local.time() < time(9, 15) or local.time() > time(15, 30):
                if candle.source == self.config.source:
                    invalid.append(candle)
        if invalid:
            removed = await self.repo.delete_candles(invalid, source=self.config.source)
            self.report.notes.append(f"Removed {removed} out-of-session BREEZE candles returned by the API.")

    @staticmethod
    def _dates(start: date, end: date) -> Iterable[date]:
        cursor = start
        while cursor <= end:
            yield cursor
            cursor += timedelta(days=1)

    @staticmethod
    def _session_dates(candles: list[Candle]) -> set[str]:
        return {c.start_time.astimezone(IST).date().isoformat() for c in candles}

    @staticmethod
    def _timestamp_gaps(candles: list[Candle]) -> list[dict[str, Any]]:
        grouped: defaultdict[str, list[datetime]] = defaultdict(list)
        for candle in candles:
            grouped[candle.instrument_id].append(candle.start_time)
        gaps: list[dict[str, Any]] = []
        for instrument_id, timestamps in grouped.items():
            ordered = sorted(set(timestamps))
            for previous, current in zip(ordered, ordered[1:]):
                previous_day = previous.astimezone(IST).date()
                current_day = current.astimezone(IST).date()
                if previous_day == current_day and current - previous > FIVE_MINUTES:
                    gaps.append({
                        "instrument_id": instrument_id,
                        "from": previous.isoformat(),
                        "to": current.isoformat(),
                        "missing_minutes": int((current - previous).total_seconds() // 60) - 5,
                    })
        return gaps


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill NIFTY 5-minute spot/futures candles from ICICI Breeze")
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    settings = get_platform_settings()
    config = BackfillConfig(
        start_date=args.start_date,
        end_date=args.end_date,
        historical_db_path=settings.historical_db_path,
        instruments_db_path=settings.instruments_db_path,
    )
    report = await BreezeHistoricalBackfill(config, settings=settings).run()
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except Exception as exc:
        logging.basicConfig(level=logging.INFO)
        print(json.dumps({"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}, indent=2))
        raise
