"""Targeted NIFTY PE 1-minute Breeze backfill for premium-based research.

The utility discovers a contiguous set of real futures trading sessions from the
local historical DB, resolves the point-in-time nearest Tuesday NIFTY weekly
expiry for each session, derives a conservative strike band from that day's
NIFTY futures range, and backfills native 1-minute PE candles from Breeze.

It is additive: existing historical candle primary keys are preserved.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
import json
import logging
import math
from pathlib import Path
import sqlite3
from typing import Any

from libs.config.settings import PlatformSettings, get_platform_settings
from libs.contracts.models import Candle, Instrument, OptionRight
from services.broker_gateway.icici_breeze_adapter import IciciBreezeAdapter
from services.broker_gateway.infrastructure.icici.response_mapper import BreezeResponseValidator
from services.historical.breeze_backfill import (
    IST,
    UTC,
    MAX_INTERVALS_PER_REQUEST,
    _breeze_timestamp,
    _number,
)
from services.historical.repository import HistoricalRepository
from services.instrument.repository import InstrumentRepository

logger = logging.getLogger(__name__)

ONE_MINUTE = timedelta(minutes=1)
EXPECTED_SESSION_ROWS = 376


@dataclass(frozen=True, slots=True)
class Pe1mBackfillConfig:
    end_date: date
    test_days: int
    warmup_days: int
    historical_db_path: Path
    instruments_db_path: Path
    target_premium: float = 400.0
    strike_step: int = 50
    lower_buffer_points: int = 300
    upper_buffer_points: int = 800
    source: str = "BREEZE"


@dataclass(slots=True)
class Pe1mBackfillReport:
    requested_end_date: str
    test_days: int
    warmup_days: int
    target_premium: float
    sessions: list[dict[str, Any]] = field(default_factory=list)
    contracts_planned: int = 0
    contracts_with_rows: int = 0
    candles_inserted: int = 0
    candles_skipped_existing: int = 0
    requests_attempted: int = 0
    requests_completed: int = 0
    empty_requests: int = 0
    rejected_rows: int = 0
    failed_requests: list[dict[str, Any]] = field(default_factory=list)
    rows_per_session: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_type": "NIFTY_PE_1M_TARGET_PREMIUM_BREEZE_BACKFILL",
            "requested_end_date": self.requested_end_date,
            "test_days": self.test_days,
            "warmup_days": self.warmup_days,
            "target_premium": self.target_premium,
            "sessions": self.sessions,
            "contracts_planned": self.contracts_planned,
            "contracts_with_rows": self.contracts_with_rows,
            "candles_inserted": self.candles_inserted,
            "candles_skipped_existing": self.candles_skipped_existing,
            "requests_attempted": self.requests_attempted,
            "requests_completed": self.requests_completed,
            "empty_requests": self.empty_requests,
            "rejected_rows": self.rejected_rows,
            "failed_requests": self.failed_requests,
            "rows_per_session": self.rows_per_session,
            "notes": self.notes,
        }


def nearest_tuesday_expiry(day: date) -> date:
    """Return the NIFTY weekly expiry on or after a trading day."""
    days_ahead = (1 - day.weekday()) % 7
    return day + timedelta(days=days_ahead)


def strike_band(
    day_low: float,
    day_high: float,
    *,
    step: int = 50,
    lower_buffer: int = 300,
    upper_buffer: int = 800,
) -> list[int]:
    """Conservative PE strike band expected to bracket a ~Rs400 premium."""
    low = int(math.floor(day_low / step) * step - lower_buffer)
    high = int(math.ceil(day_high / step) * step + upper_buffer)
    return list(range(low, high + step, step))


def option_instrument_id(expiry: date, strike: int) -> str:
    return f"INST-NIFTY-{expiry.isoformat()}-{strike}-PE"


def futures_contract_expiry(instrument_id: str) -> date | None:
    prefix = "INST-NIFTY-FUT-"
    if not instrument_id.startswith(prefix):
        return None
    try:
        return date.fromisoformat(instrument_id[len(prefix):])
    except ValueError:
        return None


def choose_futures_candidate(rows: list[dict[str, Any]], day: date) -> dict[str, Any] | None:
    """Choose one point-in-time NIFTY futures series for strike-band discovery.

    Prefer the nearest non-expired contract, then the source with the most
    1-minute rows. BREEZE wins only a row-count tie.
    """
    eligible: list[dict[str, Any]] = []
    for row in rows:
        expiry = futures_contract_expiry(str(row["instrument_id"]))
        if expiry is None or expiry < day:
            continue
        item = dict(row)
        item["contract_expiry"] = expiry
        eligible.append(item)
    if not eligible:
        return None
    nearest = min(item["contract_expiry"] for item in eligible)
    same_expiry = [item for item in eligible if item["contract_expiry"] == nearest]
    return min(
        same_expiry,
        key=lambda item: (
            -int(item["rows"]),
            0 if str(item["source"]) == "BREEZE" else 1,
            str(item["instrument_id"]),
        ),
    )


class BreezePe1mTargetBackfill:
    def __init__(
        self,
        config: Pe1mBackfillConfig,
        *,
        settings: PlatformSettings | None = None,
        adapter: IciciBreezeAdapter | None = None,
    ) -> None:
        self.config = config
        self.settings = settings or get_platform_settings()
        self.repo = HistoricalRepository(db_path=config.historical_db_path)
        self.instrument_repo = InstrumentRepository(db_path=config.instruments_db_path)
        self.adapter = adapter
        self.report = Pe1mBackfillReport(
            requested_end_date=config.end_date.isoformat(),
            test_days=config.test_days,
            warmup_days=config.warmup_days,
            target_premium=config.target_premium,
        )
        self._incoming_keys: Counter[tuple[str, str, datetime]] = Counter()

    async def run(self) -> Pe1mBackfillReport:
        if self.config.test_days <= 0:
            raise ValueError("test_days must be positive")
        if self.config.warmup_days < 0:
            raise ValueError("warmup_days must be non-negative")
        await self.repo.initialize()
        await self.instrument_repo.initialize()

        sessions = self._discover_sessions()
        if len(sessions) < self.config.test_days + self.config.warmup_days:
            raise RuntimeError(
                f"Only {len(sessions)} futures sessions found; need "
                f"{self.config.test_days + self.config.warmup_days}"
            )

        await self._connect()
        try:
            for index, session in enumerate(sessions):
                is_warmup = index < self.config.warmup_days
                await self._backfill_session(session, is_warmup=is_warmup)
        finally:
            if self.adapter is not None and self.adapter.client_manager.is_active:
                await self.adapter.client_manager.disconnect()

        self._validate_saved_rows()
        self.report.notes.extend(
            [
                "NIFTY weekly expiry is resolved as the nearest Tuesday on/after each trading session.",
                "Strike-band futures input uses the nearest non-expired futures contract, then the source with the most 1m rows; BREEZE wins only a row-count tie.",
                "Strike bands are derived from that selected stored 1m NIFTY futures range, with -300/+800 point buffers.",
                "Backfill is additive: existing historical candle primary keys are never overwritten.",
                "Warm-up sessions populate indicator history only; the backtest still evaluates the final test_days sessions.",
            ]
        )
        return self.report

    def _discover_sessions(self) -> list[dict[str, Any]]:
        needed = self.config.test_days + self.config.warmup_days
        conn = sqlite3.connect(f"file:{self.config.historical_db_path.resolve()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            dates = conn.execute(
                """
                SELECT DISTINCT substr(start_time, 1, 10) AS day
                FROM historical_candles
                WHERE interval = '1m'
                  AND instrument_id LIKE 'INST-NIFTY-FUT-%'
                  AND substr(start_time, 1, 10) <= ?
                ORDER BY day DESC
                LIMIT ?
                """,
                (self.config.end_date.isoformat(), needed),
            ).fetchall()
            selected = [str(row["day"]) for row in dates][::-1]
            sessions: list[dict[str, Any]] = []
            for day_text in selected:
                source_rows = conn.execute(
                    """
                    SELECT instrument_id, source, COUNT(*) AS rows,
                           MIN(low) AS day_low, MAX(high) AS day_high
                    FROM historical_candles
                    WHERE interval = '1m'
                      AND instrument_id LIKE 'INST-NIFTY-FUT-%'
                      AND substr(start_time, 1, 10) = ?
                      AND source IN ('BREEZE', 'KITE')
                    GROUP BY instrument_id, source
                    """,
                    (day_text,),
                ).fetchall()
                if not source_rows:
                    continue
                day = date.fromisoformat(day_text)
                row = choose_futures_candidate([dict(x) for x in source_rows], day)
                if row is None:
                    continue
                strikes = strike_band(
                    float(row["day_low"]),
                    float(row["day_high"]),
                    step=self.config.strike_step,
                    lower_buffer=self.config.lower_buffer_points,
                    upper_buffer=self.config.upper_buffer_points,
                )
                sessions.append(
                    {
                        "date": day,
                        "futures_instrument": str(row["instrument_id"]),
                        "futures_source": str(row["source"]),
                        "futures_rows": int(row["rows"]),
                        "futures_low": float(row["day_low"]),
                        "futures_high": float(row["day_high"]),
                        "expiry": nearest_tuesday_expiry(day),
                        "strikes": strikes,
                    }
                )
            return sessions
        finally:
            conn.close()

    async def _connect(self) -> None:
        if self.adapter is None:
            self.adapter = IciciBreezeAdapter(
                api_key=(
                    self.settings.breeze_api_key.get_secret_value()
                    if self.settings.breeze_api_key
                    else ""
                ),
                secret_key=(
                    self.settings.breeze_secret_key.get_secret_value()
                    if self.settings.breeze_secret_key
                    else ""
                ),
                session_token=(
                    self.settings.breeze_session_token.get_secret_value()
                    if self.settings.breeze_session_token
                    else ""
                ),
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

    async def _backfill_session(
        self,
        session: dict[str, Any],
        *,
        is_warmup: bool,
    ) -> None:
        day: date = session["date"]
        expiry: date = session["expiry"]
        strikes: list[int] = session["strikes"]
        self.report.contracts_planned += len(strikes)
        session_rows = 0

        self.report.sessions.append(
            {
                "date": day.isoformat(),
                "role": "WARMUP" if is_warmup else "TEST",
                "expiry": expiry.isoformat(),
                "futures_instrument": session["futures_instrument"],
                "futures_source": session["futures_source"],
                "futures_low": session["futures_low"],
                "futures_high": session["futures_high"],
                "strike_low": strikes[0],
                "strike_high": strikes[-1],
                "strike_count": len(strikes),
            }
        )

        for strike in strikes:
            instrument_id = option_instrument_id(expiry, strike)
            await self._ensure_instrument(instrument_id, expiry, strike)
            rows = await self._request_day(
                instrument_id=instrument_id,
                day=day,
                expiry=expiry,
                strike=strike,
            )
            candles = self._normalize_rows(
                rows,
                instrument_id=instrument_id,
                day=day,
            )
            if candles:
                self.report.contracts_with_rows += 1
                session_rows += len(candles)
                await self._save_additive(candles)

        self.report.rows_per_session[day.isoformat()] = session_rows

    async def _ensure_instrument(
        self,
        instrument_id: str,
        expiry: date,
        strike: int,
    ) -> None:
        existing = await self.instrument_repo.get_by_id(instrument_id)
        if existing is not None:
            return
        await self.instrument_repo.save_instrument(
            Instrument(
                instrument_id=instrument_id,
                broker="ICICI_BREEZE",
                exchange="NFO",
                segment="OPTIONS",
                underlying="NIFTY",
                stock_code="NIFTY",
                expiry=expiry.isoformat(),
                strike=float(strike),
                option_right=OptionRight.PUT,
                lot_size=65,
                tick_size=0.05,
                tradable=False,
            )
        )

    async def _request_day(
        self,
        *,
        instrument_id: str,
        day: date,
        expiry: date,
        strike: int,
    ) -> list[dict[str, Any]]:
        assert self.adapter is not None
        self.report.requests_attempted += 1
        try:
            await self.adapter.rate_limiter.acquire_read()
            sdk = self.adapter.client_manager.get_sdk_client()
            raw = await self.adapter.sdk_runner.run(
                lambda: sdk.get_historical_data_v2(
                    interval="1minute",
                    from_date=f"{day.isoformat()}T09:15:00.000Z",
                    to_date=f"{day.isoformat()}T15:30:00.000Z",
                    stock_code="NIFTY",
                    exchange_code="NFO",
                    product_type="options",
                    expiry_date=f"{expiry.isoformat()}T06:00:00.000Z",
                    right="put",
                    strike_price=str(strike),
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
                    f"Breeze returned {len(data)} rows, exceeding {MAX_INTERVALS_PER_REQUEST}"
                )
            self.report.requests_completed += 1
            if not data:
                self.report.empty_requests += 1
            return [row for row in data if isinstance(row, dict)]
        except Exception as exc:
            failure = {
                "instrument_id": instrument_id,
                "date": day.isoformat(),
                "expiry": expiry.isoformat(),
                "strike": strike,
                "error": f"{type(exc).__name__}: {exc}",
            }
            self.report.failed_requests.append(failure)
            logger.error("Breeze PE 1m request failed: %s", failure)
            return []

    def _normalize_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        instrument_id: str,
        day: date,
    ) -> list[Candle]:
        start_utc = datetime.combine(day, time(9, 15), tzinfo=IST).astimezone(UTC)
        end_utc = datetime.combine(day, time(15, 30), tzinfo=IST).astimezone(UTC)
        result: list[Candle] = []
        for row in rows:
            try:
                start_time = _breeze_timestamp(row.get("datetime") or row.get("date"))
                local = start_time.astimezone(IST)
                if not (start_utc <= start_time <= end_utc):
                    raise ValueError("timestamp outside requested session")
                if local.date() != day:
                    raise ValueError("timestamp on wrong exchange date")
                if local.time() < time(9, 15) or local.time() > time(15, 30):
                    raise ValueError("timestamp outside exchange session")
                if start_time.second != 0:
                    raise ValueError("timestamp not aligned to minute")

                open_price = _number(row, "open")
                high_price = _number(row, "high")
                low_price = _number(row, "low")
                close_price = _number(row, "close")
                volume = _number(row, "volume", required=False)
                open_interest = _number(row, "open_interest", required=False)
                assert None not in (open_price, high_price, low_price, close_price)
                if (
                    low_price > high_price
                    or not low_price <= open_price <= high_price
                    or not low_price <= close_price <= high_price
                    or low_price <= 0
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
                        open=float(open_price),
                        high=float(high_price),
                        low=float(low_price),
                        close=float(close_price),
                        volume=int(volume or 0),
                        open_interest=int(open_interest or 0),
                        source=self.config.source,
                    )
                )
            except Exception as exc:
                self.report.rejected_rows += 1
                logger.warning(
                    "Rejected Breeze PE row for %s on %s: %s",
                    instrument_id,
                    day,
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
        missing = [
            candle
            for candle in candles
            if (candle.instrument_id, candle.interval, candle.start_time.isoformat())
            not in existing
        ]
        self.report.candles_skipped_existing += len(candles) - len(missing)
        if missing:
            await self.repo.save_candles(missing)
            self.report.candles_inserted += len(missing)

    def _validate_saved_rows(self) -> None:
        conn = sqlite3.connect(
            f"file:{self.config.historical_db_path.resolve()}?mode=ro",
            uri=True,
        )
        try:
            for session in self.report.sessions:
                day = session["date"]
                expiry = session["expiry"]
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS rows,
                           MIN(close) AS min_close,
                           MAX(close) AS max_close,
                           MIN(ABS(close - ?)) AS closest_distance
                    FROM historical_candles
                    WHERE interval = '1m'
                      AND source = ?
                      AND substr(start_time, 1, 10) = ?
                      AND instrument_id LIKE ?
                    """,
                    (
                        self.config.target_premium,
                        self.config.source,
                        day,
                        f"INST-NIFTY-{expiry}-%-PE",
                    ),
                ).fetchone()
                session["saved_rows"] = int(row[0] or 0)
                session["min_close"] = float(row[1]) if row[1] is not None else None
                session["max_close"] = float(row[2]) if row[2] is not None else None
                session["closest_to_target"] = (
                    float(row[3]) if row[3] is not None else None
                )
                session["target_bracketed"] = bool(
                    row[1] is not None
                    and row[2] is not None
                    and float(row[1]) <= self.config.target_premium <= float(row[2])
                )
        finally:
            conn.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill NIFTY PE native 1m candles around a target premium"
    )
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument("--test-days", type=int, default=10)
    parser.add_argument("--warmup-days", type=int, default=5)
    parser.add_argument("--target-premium", type=float, default=400.0)
    parser.add_argument("--lower-buffer-points", type=int, default=300)
    parser.add_argument("--upper-buffer-points", type=int, default=800)
    parser.add_argument("--report-path", type=Path, default=None)
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    settings = get_platform_settings()
    config = Pe1mBackfillConfig(
        end_date=args.end_date,
        test_days=args.test_days,
        warmup_days=args.warmup_days,
        historical_db_path=settings.historical_db_path,
        instruments_db_path=settings.instruments_db_path,
        target_premium=args.target_premium,
        lower_buffer_points=args.lower_buffer_points,
        upper_buffer_points=args.upper_buffer_points,
    )
    report = await BreezePe1mTargetBackfill(config, settings=settings).run()
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
                {"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"},
                indent=2,
            )
        )
        raise