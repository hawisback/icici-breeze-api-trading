"""Additive NIFTY futures 1-minute historical backfill from ICICI Breeze.

This utility exists specifically to support deterministic 2-minute research
bars. Breeze exposes native 1-minute history; 2-minute candles must be derived
from pairs of those 1-minute candles, never inferred from 5-minute OHLC.

The backfill:
- authenticates directly with Breeze using platform settings,
- preserves the existing futures contract IDs,
- fetches only NIFTY futures (no spot),
- chunks requests conservatively below Breeze's ~1000-row response limit,
- writes only missing (instrument, interval, start_time) keys,
- never modifies existing 5m/15m candles.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
import json
import logging
from pathlib import Path
from typing import Any

from libs.config.settings import PlatformSettings, get_platform_settings
from libs.contracts.models import Candle, Instrument
from services.broker_gateway.icici_breeze_adapter import IciciBreezeAdapter
from services.broker_gateway.infrastructure.icici.response_mapper import BreezeResponseValidator
from services.historical.breeze_backfill import (
    IST,
    UTC,
    MAX_INTERVALS_PER_REQUEST,
    _breeze_timestamp,
    _number,
    futures_contract_periods,
    split_date_range,
)
from services.historical.repository import HistoricalRepository
from services.instrument.repository import InstrumentRepository

logger = logging.getLogger(__name__)

ONE_MINUTE = timedelta(minutes=1)
MAX_CALENDAR_DAYS_PER_REQUEST_1M = 2


@dataclass(frozen=True, slots=True)
class Futures1mBackfillConfig:
    start_date: date
    end_date: date
    historical_db_path: Path
    instruments_db_path: Path
    source: str = "BREEZE"


@dataclass(slots=True)
class Futures1mBackfillReport:
    requested_start: str
    requested_end: str
    futures_candles: int = 0
    futures_sessions: int = 0
    futures_contracts: dict[str, int] = field(default_factory=dict)
    futures_contract_ids: dict[str, str] = field(default_factory=dict)
    candles_inserted: int = 0
    candles_skipped_existing: int = 0
    rejected_rows: int = 0
    duplicate_rows_received: int = 0
    requests_attempted: int = 0
    requests_completed: int = 0
    failed_requests: list[dict[str, Any]] = field(default_factory=list)
    rows_per_session: dict[str, int] = field(default_factory=dict)
    thin_sessions: list[dict[str, Any]] = field(default_factory=list)
    actual_start: str | None = None
    actual_end: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_type": "NIFTY_FUTURES_1M_BREEZE_ADDITIVE_BACKFILL",
            "requested_date_range": {
                "start": self.requested_start,
                "end": self.requested_end,
            },
            "actual_date_range_received": {
                "start": self.actual_start,
                "end": self.actual_end,
            },
            "futures_candles": self.futures_candles,
            "futures_sessions": self.futures_sessions,
            "futures_contracts": self.futures_contracts,
            "futures_contract_ids": self.futures_contract_ids,
            "candles_inserted": self.candles_inserted,
            "candles_skipped_existing": self.candles_skipped_existing,
            "rejected_rows": self.rejected_rows,
            "duplicate_rows_received": self.duplicate_rows_received,
            "requests_attempted": self.requests_attempted,
            "requests_completed": self.requests_completed,
            "failed_requests": self.failed_requests,
            "rows_per_session": self.rows_per_session,
            "thin_sessions": self.thin_sessions,
            "notes": self.notes,
        }


class BreezeFutures1mBackfill:
    def __init__(
        self,
        config: Futures1mBackfillConfig,
        *,
        settings: PlatformSettings | None = None,
        adapter: IciciBreezeAdapter | None = None,
    ) -> None:
        self.config = config
        self.settings = settings or get_platform_settings()
        self.repo = HistoricalRepository(db_path=config.historical_db_path)
        self.instrument_repo = InstrumentRepository(db_path=config.instruments_db_path)
        self.adapter = adapter
        self.report = Futures1mBackfillReport(
            requested_start=config.start_date.isoformat(),
            requested_end=config.end_date.isoformat(),
        )
        self._incoming_keys: Counter[tuple[str, str, datetime]] = Counter()

    async def run(self) -> Futures1mBackfillReport:
        if self.config.end_date < self.config.start_date:
            raise ValueError("end date must not precede start date")
        await self.repo.initialize()
        await self.instrument_repo.initialize()
        await self._connect()
        await self._ensure_futures_instruments()
        try:
            await self._fetch_futures()
        finally:
            if self.adapter is not None and self.adapter.client_manager.is_active:
                await self.adapter.client_manager.disconnect()
        await self._validate()
        return self.report

    async def _connect(self) -> None:
        if self.adapter is None:
            self.adapter = IciciBreezeAdapter(
                api_key=self.settings.breeze_api_key.get_secret_value()
                if self.settings.breeze_api_key else "",
                secret_key=self.settings.breeze_secret_key.get_secret_value()
                if self.settings.breeze_secret_key else "",
                session_token=self.settings.breeze_session_token.get_secret_value()
                if self.settings.breeze_session_token else "",
            )
        ok = await self.adapter.authenticate(
            self.adapter.api_key,
            self.adapter.secret_key,
            self.adapter.session_token,
        )
        if not ok or not self.adapter.client_manager.is_active:
            raise RuntimeError(
                "Breeze authentication failed; refresh the daily Breeze session token first"
            )

    async def _ensure_futures_instruments(self) -> None:
        for _, _, expiry in futures_contract_periods(
            self.config.start_date,
            self.config.end_date,
        ):
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

    async def _fetch_futures(self) -> None:
        for period_start, period_end, expiry in futures_contract_periods(
            self.config.start_date,
            self.config.end_date,
        ):
            instrument_id = f"INST-NIFTY-FUT-{expiry.isoformat()}"
            for chunk_start, chunk_end in split_date_range(
                period_start,
                period_end,
                max_days=MAX_CALENDAR_DAYS_PER_REQUEST_1M,
            ):
                rows = await self._request_chunk(
                    instrument_id=instrument_id,
                    expiry=expiry,
                    chunk_start=chunk_start,
                    chunk_end=chunk_end,
                )
                candles = self._normalize_rows(
                    rows,
                    instrument_id=instrument_id,
                    window_start=self._session_start_utc(chunk_start),
                    window_end=self._session_end_utc(chunk_end),
                )
                await self._save_additive(candles)
                self.report.futures_contracts[expiry.isoformat()] = (
                    self.report.futures_contracts.get(expiry.isoformat(), 0)
                    + len(candles)
                )

    async def _request_chunk(
        self,
        *,
        instrument_id: str,
        expiry: date,
        chunk_start: date,
        chunk_end: date,
    ) -> list[dict[str, Any]]:
        assert self.adapter is not None
        self.report.requests_attempted += 1
        # Two full exchange sessions contain ~750 one-minute bars, safely under
        # the endpoint's practical 1000-row response ceiling.
        estimated_rows = ((chunk_end - chunk_start).days + 1) * 376
        if estimated_rows > MAX_INTERVALS_PER_REQUEST:
            raise AssertionError(
                f"1m chunk exceeds Breeze response budget: {chunk_start}..{chunk_end}"
            )
        try:
            await self.adapter.rate_limiter.acquire_read()
            sdk = self.adapter.client_manager.get_sdk_client()
            raw = await self.adapter.sdk_runner.run(
                lambda: sdk.get_historical_data_v2(
                    interval="1minute",
                    from_date=f"{chunk_start.isoformat()}T09:15:00.000Z",
                    to_date=f"{chunk_end.isoformat()}T15:30:00.000Z",
                    stock_code="NIFTY",
                    exchange_code="NFO",
                    product_type="futures",
                    expiry_date=f"{expiry.isoformat()}T06:00:00.000Z",
                    right="others",
                    strike_price="0",
                ),
                timeout_sec=30.0,
            )
            data = BreezeResponseValidator.unwrap_success(raw)
            if not isinstance(data, list):
                raise ValueError(
                    f"Breeze returned non-list Success payload: {type(data).__name__}"
                )
            if len(data) > MAX_INTERVALS_PER_REQUEST:
                raise ValueError(
                    f"Breeze returned {len(data)} rows, exceeding "
                    f"{MAX_INTERVALS_PER_REQUEST}"
                )
            self.report.requests_completed += 1
            return [row for row in data if isinstance(row, dict)]
        except Exception as exc:
            failure = {
                "instrument_id": instrument_id,
                "expiry": expiry.isoformat(),
                "start": chunk_start.isoformat(),
                "end": chunk_end.isoformat(),
                "error": f"{type(exc).__name__}: {exc}",
            }
            self.report.failed_requests.append(failure)
            logger.error("Breeze 1m futures request failed: %s", failure)
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
        *,
        instrument_id: str,
        window_start: datetime,
        window_end: datetime,
    ) -> list[Candle]:
        result: list[Candle] = []
        for row in rows:
            try:
                start_time = _breeze_timestamp(
                    row.get("datetime") or row.get("date")
                )
                local = start_time.astimezone(IST)
                if not (window_start <= start_time <= window_end):
                    raise ValueError("timestamp outside requested window")
                if local.weekday() >= 5:
                    raise ValueError("weekend timestamp")
                if local.time() < time(9, 15) or local.time() > time(15, 30):
                    raise ValueError("timestamp outside exchange session")
                if start_time.second != 0:
                    raise ValueError("timestamp not aligned to a minute")

                open_price = _number(row, "open")
                high_price = _number(row, "high")
                low_price = _number(row, "low")
                close_price = _number(row, "close")
                volume = _number(row, "volume", required=False)
                open_interest = _number(row, "open_interest", required=False)
                assert (
                    open_price is not None
                    and high_price is not None
                    and low_price is not None
                    and close_price is not None
                )
                if (
                    low_price > high_price
                    or not (low_price <= open_price <= high_price)
                    or not (low_price <= close_price <= high_price)
                ):
                    raise ValueError("invalid OHLC relationship")
                if volume is not None and volume < 0:
                    raise ValueError("negative volume")
                if open_interest is not None and open_interest < 0:
                    raise ValueError("negative open interest")

                key = (instrument_id, "1m", start_time)
                self._incoming_keys[key] += 1
                result.append(
                    Candle(
                        instrument_id=instrument_id,
                        interval="1m",
                        start_time=start_time,
                        end_time=start_time + ONE_MINUTE,
                        open=open_price,
                        high=high_price,
                        low=low_price,
                        close=close_price,
                        volume=int(volume or 0),
                        open_interest=(
                            int(open_interest)
                            if open_interest is not None
                            else 0
                        ),
                        source=self.config.source,
                    )
                )
            except Exception as exc:
                self.report.rejected_rows += 1
                logger.warning(
                    "Rejected Breeze 1m futures row for %s: %s",
                    instrument_id,
                    exc,
                )
        return result

    async def _save_additive(self, candles: list[Candle]) -> None:
        if not candles:
            return
        existing = await self.repo.get_existing_candle_keys(
            candles[0].instrument_id,
            "1m",
            start_time=min(c.start_time for c in candles),
            end_time=max(c.start_time for c in candles),
        )
        seen: set[tuple[str, str, str]] = set()
        new_rows: list[Candle] = []
        for candle in candles:
            key = (
                candle.instrument_id,
                candle.interval,
                candle.start_time.isoformat(),
            )
            if key in existing or key in seen:
                self.report.candles_skipped_existing += 1
                continue
            seen.add(key)
            new_rows.append(candle)
        await self.repo.save_candles(new_rows)
        self.report.candles_inserted += len(new_rows)

    async def _validate(self) -> None:
        futures: list[Candle] = []
        for instrument_id in self.report.futures_contract_ids.values():
            futures.extend(
                await self.repo.get_candles(
                    instrument_id,
                    "1m",
                    limit=5_000_000,
                )
            )
        futures = [
            candle
            for candle in futures
            if self.config.start_date
            <= candle.start_time.astimezone(IST).date()
            <= self.config.end_date
        ]
        futures.sort(key=lambda candle: candle.start_time)
        self.report.futures_candles = len(futures)
        if futures:
            self.report.actual_start = (
                futures[0].start_time.astimezone(IST).date().isoformat()
            )
            self.report.actual_end = (
                futures[-1].start_time.astimezone(IST).date().isoformat()
            )

        counts: dict[str, int] = defaultdict(int)
        for candle in futures:
            counts[candle.start_time.astimezone(IST).date().isoformat()] += 1
        self.report.rows_per_session = dict(sorted(counts.items()))
        self.report.futures_sessions = len(counts)
        self.report.thin_sessions = [
            {"date": day, "rows": rows}
            for day, rows in sorted(counts.items())
            if rows < 360
        ]
        self.report.duplicate_rows_received = sum(
            max(0, count - 1)
            for count in self._incoming_keys.values()
        )
        self.report.notes.extend(
            [
                "1m backfill is additive and does not overwrite 5m/15m history.",
                "Sessions with fewer than 360 one-minute rows are flagged as thin.",
                "2m candles should be derived deterministically from 1m pairs during research/replay.",
            ]
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill additive NIFTY futures 1-minute candles from Breeze"
    )
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument("--report-path", type=Path, default=None)
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    settings = get_platform_settings()
    config = Futures1mBackfillConfig(
        start_date=args.start_date,
        end_date=args.end_date,
        historical_db_path=settings.historical_db_path,
        instruments_db_path=settings.instruments_db_path,
    )
    report = await BreezeFutures1mBackfill(
        config,
        settings=settings,
    ).run()
    payload = report.to_dict()
    if args.report_path:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except Exception as exc:
        logging.basicConfig(level=logging.INFO)
        print(
            json.dumps(
                {
                    "status": "FAILED",
                    "error": f"{type(exc).__name__}: {exc}",
                },
                indent=2,
            )
        )
        raise
