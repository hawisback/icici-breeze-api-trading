"""Read-only Strategy A historical-data audit.

This utility inspects the exact SQLite historical database used by replay.
It never calls a broker, never inserts/replaces candles, and never evaluates
Strategy A trading rules. Its job is limited to data coverage and readiness.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from libs.contracts.models import Candle
from services.strategy.futures_signal import (
    FuturesContractResolver,
    aggregate_completed_15m,
    canonical_active_futures_stream,
    contract_expiry,
)

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
SESSION_START = time(9, 15)
RAW_SESSION_LAST_START = time(15, 25)
OPTIONAL_CLOSE_MARKER = time(15, 30)
ENTRY_FIRST_END = time(9, 45)
ENTRY_LAST_END = time(14, 45)
WARMUP_CALENDAR_DAYS = 7
EMA50_MIN_BARS = 50
ADX14_MIN_BARS = 28


def _default_db_path() -> Path:
    data_root = Path(os.environ.get("DATA_ROOT", "./data")).resolve()
    return data_root / "market" / "historical.db"


def _open_read_only(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"historical database not found: {db_path}")
    conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _source_predicate(source: str, column: str = "source") -> tuple[str, list[str]]:
    normalized = source.upper()
    if normalized == "MIXED":
        return f"{column} IN ('BREEZE', 'KITE', 'LIVE')", []
    if normalized not in {"BREEZE", "KITE", "LIVE"}:
        raise ValueError("source must be BREEZE, KITE, LIVE, or MIXED")
    return f"{column} = ?", [normalized]


def _aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _row_to_candle(row: sqlite3.Row) -> Candle:
    return Candle(
        instrument_id=row["instrument_id"],
        interval=row["interval"],
        start_time=_aware(row["start_time"]),
        end_time=_aware(row["end_time"]),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=int(row["volume"]),
        open_interest=int(row["open_interest"]),
        source=row["source"],
    )


def _session_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, SESSION_START, tzinfo=IST)
    end_exclusive = datetime.combine(day, OPTIONAL_CLOSE_MARKER, tzinfo=IST) + timedelta(minutes=5)
    return start.astimezone(UTC), end_exclusive.astimezone(UTC)


def _expected_5m_starts(day: date) -> list[datetime]:
    cursor = datetime.combine(day, SESSION_START, tzinfo=IST)
    last = datetime.combine(day, RAW_SESSION_LAST_START, tzinfo=IST)
    result: list[datetime] = []
    while cursor <= last:
        result.append(cursor)
        cursor += timedelta(minutes=5)
    return result


def _expected_entry_15m_ends(day: date) -> list[datetime]:
    cursor = datetime.combine(day, ENTRY_FIRST_END, tzinfo=IST)
    last = datetime.combine(day, ENTRY_LAST_END, tzinfo=IST)
    result: list[datetime] = []
    while cursor <= last:
        result.append(cursor)
        cursor += timedelta(minutes=15)
    return result


def _load_rows(
    conn: sqlite3.Connection,
    *,
    start_utc: datetime,
    end_utc: datetime,
    source: str,
    instrument_id: str | None = None,
    instrument_like: str | None = None,
) -> list[Candle]:
    clauses = [
        "interval = '5m'",
        "start_time >= ?",
        "start_time < ?",
    ]
    params: list[Any] = [start_utc.isoformat(), end_utc.isoformat()]
    if instrument_id is not None:
        clauses.append("instrument_id = ?")
        params.append(instrument_id)
    elif instrument_like is not None:
        clauses.append("instrument_id LIKE ?")
        params.append(instrument_like)
    else:
        raise ValueError("instrument_id or instrument_like is required")
    source_clause, source_params = _source_predicate(source)
    clauses.append(source_clause)
    params.extend(source_params)
    rows = conn.execute(
        f"""
        SELECT instrument_id, interval, start_time, end_time,
               open, high, low, close, volume, open_interest, source
        FROM historical_candles
        WHERE {' AND '.join(clauses)}
        ORDER BY start_time ASC
        """,
        params,
    ).fetchall()
    return [_row_to_candle(row) for row in rows]


def _latest_spot_session_dates(
    conn: sqlite3.Connection,
    *,
    sessions: int,
    source: str,
) -> list[date]:
    source_clause, source_params = _source_predicate(source)
    rows = conn.execute(
        f"""
        SELECT start_time
        FROM historical_candles
        WHERE instrument_id = 'INST-NIFTY-INDEX'
          AND interval = '5m'
          AND {source_clause}
        ORDER BY start_time DESC
        """,
        source_params,
    ).fetchall()
    dates: list[date] = []
    seen: set[date] = set()
    for row in rows:
        local_day = _aware(row["start_time"]).astimezone(IST).date()
        if local_day in seen:
            continue
        seen.add(local_day)
        dates.append(local_day)
        if len(dates) >= sessions:
            break
    return dates


def _active_contract(day: date, candles: Iterable[Candle]) -> str | None:
    contracts = {
        candle.instrument_id: contract_expiry(candle.instrument_id)
        for candle in candles
        if "NIFTY-FUT-" in candle.instrument_id.upper()
    }
    if not contracts:
        return None
    as_of = datetime.combine(day, SESSION_START, tzinfo=IST)
    return FuturesContractResolver.resolve_contracts(contracts, as_of=as_of)


def _invalid_ohlc_count(candles: Iterable[Candle]) -> int:
    count = 0
    for candle in candles:
        if (
            candle.low > candle.high
            or candle.open < candle.low
            or candle.open > candle.high
            or candle.close < candle.low
            or candle.close > candle.high
            or candle.volume < 0
            or (candle.open_interest is not None and candle.open_interest < 0)
        ):
            count += 1
    return count


def _missing_times(actual: Iterable[Candle], expected_local: Iterable[datetime]) -> list[str]:
    actual_local = {
        candle.start_time.astimezone(IST).replace(second=0, microsecond=0)
        for candle in actual
    }
    return [value.isoformat() for value in expected_local if value not in actual_local]


def _nonzero_pct(values: Iterable[int | None]) -> float:
    materialized = list(values)
    if not materialized:
        return 0.0
    return round(sum(value not in (None, 0) for value in materialized) / len(materialized) * 100, 2)


def audit_session(conn: sqlite3.Connection, day: date, *, source: str) -> dict[str, Any]:
    session_start_utc, session_end_utc = _session_bounds(day)
    spot = _load_rows(
        conn,
        start_utc=session_start_utc,
        end_utc=session_end_utc,
        source=source,
        instrument_id="INST-NIFTY-INDEX",
    )
    all_futures = _load_rows(
        conn,
        start_utc=session_start_utc,
        end_utc=session_end_utc,
        source=source,
        instrument_like="INST-NIFTY-FUT-%",
    )
    contract_id = _active_contract(day, all_futures)
    futures = [candle for candle in all_futures if candle.instrument_id == contract_id]

    expected_5m = _expected_5m_starts(day)
    spot_missing = _missing_times(spot, expected_5m)
    futures_missing = _missing_times(futures, expected_5m)

    as_of = datetime.combine(day, OPTIONAL_CLOSE_MARKER, tzinfo=IST) + timedelta(minutes=5)
    bars_15m = aggregate_completed_15m(futures, as_of=as_of)
    expected_entry_ends = _expected_entry_15m_ends(day)
    available_entry_ends = {
        candle.end_time.astimezone(IST).replace(second=0, microsecond=0)
        for candle in bars_15m
    }
    missing_entry_ends = [
        value.isoformat()
        for value in expected_entry_ends
        if value not in available_entry_ends
    ]

    first_decision = datetime.combine(day, ENTRY_FIRST_END, tzinfo=IST)
    warmup_start = datetime.combine(
        day - timedelta(days=WARMUP_CALENDAR_DAYS),
        SESSION_START,
        tzinfo=IST,
    )
    warmup_futures = (
        _load_rows(
            conn,
            start_utc=warmup_start.astimezone(UTC),
            end_utc=(first_decision + timedelta(minutes=5)).astimezone(UTC),
            source=source,
            instrument_like="INST-NIFTY-FUT-%",
        )
        if contract_id
        else []
    )
    warmup_15m_all = aggregate_completed_15m(warmup_futures, as_of=first_decision)
    warmup_15m = canonical_active_futures_stream(
        warmup_15m_all,
        as_of=first_decision,
        interval="15m",
    )
    warmup_count = len(warmup_15m)

    close_marker = datetime.combine(day, OPTIONAL_CLOSE_MARKER, tzinfo=IST)
    has_spot_close_marker = any(
        candle.start_time.astimezone(IST).replace(second=0, microsecond=0) == close_marker
        for candle in spot
    )
    has_futures_close_marker = any(
        candle.start_time.astimezone(IST).replace(second=0, microsecond=0) == close_marker
        for candle in futures
    )

    reasons: list[str] = []
    if not spot:
        reasons.append("SPOT_SESSION_MISSING")
    if contract_id is None:
        reasons.append("ACTIVE_FUTURES_CONTRACT_MISSING")
    if futures_missing:
        reasons.append("FUTURES_5M_GAPS")
    if spot_missing:
        reasons.append("SPOT_5M_GAPS")
    if missing_entry_ends:
        reasons.append("STRATEGY_A_15M_ENTRY_WINDOW_GAPS")
    if warmup_count < EMA50_MIN_BARS:
        reasons.append("EMA50_WARMUP_INSUFFICIENT")
    if warmup_count < ADX14_MIN_BARS:
        reasons.append("ADX14_WARMUP_INSUFFICIENT")
    if _invalid_ohlc_count(futures):
        reasons.append("INVALID_FUTURES_OHLC")

    data_ready = not reasons
    return {
        "date": day.isoformat(),
        "source": source.upper(),
        "active_futures_contract": contract_id,
        "spot": {
            "rows": len(spot),
            "required_5m_rows": len(expected_5m),
            "missing_required_5m_starts_ist": spot_missing,
            "has_optional_1530_marker": has_spot_close_marker,
        },
        "futures": {
            "rows": len(futures),
            "all_contract_rows": len(all_futures),
            "required_5m_rows": len(expected_5m),
            "missing_required_5m_starts_ist": futures_missing,
            "invalid_ohlc_rows": _invalid_ohlc_count(futures),
            "volume_nonzero_pct": _nonzero_pct(candle.volume for candle in futures),
            "open_interest_nonzero_pct": _nonzero_pct(candle.open_interest for candle in futures),
            "has_optional_1530_marker": has_futures_close_marker,
        },
        "strategy_a_input_readiness": {
            "complete_15m_bars_in_session": len(bars_15m),
            "expected_entry_window_15m_bars": len(expected_entry_ends),
            "available_entry_window_15m_bars": len(expected_entry_ends) - len(missing_entry_ends),
            "missing_entry_window_15m_ends_ist": missing_entry_ends,
            "entry_window_coverage_pct": round(
                (len(expected_entry_ends) - len(missing_entry_ends))
                / len(expected_entry_ends)
                * 100,
                2,
            ),
            "warmup_calendar_days": WARMUP_CALENDAR_DAYS,
            "warmup_15m_bars_at_first_decision": warmup_count,
            "ema50_minimum_bars": EMA50_MIN_BARS,
            "adx14_minimum_bars": ADX14_MIN_BARS,
        },
        "data_ready": data_ready,
        "refetch_recommended": bool(
            futures_missing
            or spot_missing
            or missing_entry_ends
            or _invalid_ohlc_count(futures)
            or not futures
            or warmup_count < EMA50_MIN_BARS
            or warmup_count < ADX14_MIN_BARS
        ),
        "reasons": reasons,
    }


def audit_database(
    db_path: Path,
    *,
    sessions: int = 10,
    source: str = "BREEZE",
) -> dict[str, Any]:
    if sessions < 1:
        raise ValueError("sessions must be at least 1")
    conn = _open_read_only(db_path)
    try:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        if "historical_candles" not in tables:
            raise RuntimeError("historical_candles table is missing")

        dates = _latest_spot_session_dates(conn, sessions=sessions, source=source)
        session_reports = [audit_session(conn, day, source=source) for day in dates]
    finally:
        conn.close()

    failed = [item for item in session_reports if not item["data_ready"]]
    refetch = [item for item in session_reports if item["refetch_recommended"]]
    return {
        "audit_type": "STRATEGY_A_HISTORICAL_DATA_READ_ONLY",
        "db_path": str(db_path.resolve()),
        "source": source.upper(),
        "sessions_requested": sessions,
        "sessions_found": len(session_reports),
        "latest_session": session_reports[0]["date"] if session_reports else None,
        "all_sessions_data_ready": bool(session_reports) and not failed,
        "refetch_recommended": bool(refetch),
        "refetch_dates": [item["date"] for item in refetch],
        "failed_readiness_dates": [item["date"] for item in failed],
        "sessions": session_reports,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only audit of Strategy A spot/futures replay inputs"
    )
    parser.add_argument("--db-path", type=Path, default=_default_db_path())
    parser.add_argument("--sessions", type=int, default=10)
    parser.add_argument(
        "--source",
        choices=("BREEZE", "KITE", "LIVE", "MIXED"),
        default="BREEZE",
    )
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    report = audit_database(
        args.db_path,
        sessions=args.sessions,
        source=args.source,
    )
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
