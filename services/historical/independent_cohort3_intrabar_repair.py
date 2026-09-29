"""Repair incomplete Cohort-3 one-minute futures sessions without fabrication.

Reads the already-collected Cohort-3 intrabar artifact, identifies sessions that
are not an exact 09:15..15:29 set of 375 one-minute bars, and refetches only
those sessions from Breeze using the frozen contract identity from the market
artifact. A session is replaced only by a complete, valid 375-row refetch.
No interpolation, forward fill, OHLC aggregation, contract substitution, or
synthetic rows are permitted.
"""
from __future__ import annotations

import argparse
import json
import time as time_module
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from services.historical.independent_cohort3_protocol import (
    EXPECTED,
    SESSION_DATES,
    validate_intrabar,
    validate_market,
)
from services.historical.independent_futures_intrabar_research import (
    IST,
    SESSION_END,
    SESSION_START,
    _contract_by_date,
    _load_local_env,
    _number,
    _secret,
)


def _expected_timestamps(day: str) -> set[str]:
    session_date = date.fromisoformat(day)
    start = datetime.combine(session_date, SESSION_START, tzinfo=IST)
    return {
        (start + timedelta(minutes=offset)).isoformat()
        for offset in range(int(EXPECTED["one_minute_bars_per_session"]))
    }


def _normalize_success(
    success: list[dict[str, Any]],
    *,
    day: str,
    expiry: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows: list[dict[str, Any]] = []
    rejected_bad_timestamp = 0
    rejected_outside_session = 0
    rejected_missing_fields = 0
    for item in success:
        try:
            ts = datetime.fromisoformat(str(item.get("datetime", "")))
        except ValueError:
            rejected_bad_timestamp += 1
            continue
        ts = ts.replace(tzinfo=IST) if ts.tzinfo is None else ts.astimezone(IST)
        ts = ts.replace(second=0, microsecond=0)
        if ts.date().isoformat() != day or not (
            SESSION_START <= ts.time() < SESSION_END
        ):
            rejected_outside_session += 1
            continue
        values = [
            _number(item.get(name))
            for name in ("open", "high", "low", "close", "volume", "open_interest")
        ]
        if any(value is None for value in values):
            rejected_missing_fields += 1
            continue
        rows.append(
            {
                "timestamp": ts.isoformat(),
                "open": values[0],
                "high": values[1],
                "low": values[2],
                "close": values[3],
                "volume": values[4],
                "open_interest": values[5],
                "expiry": expiry,
                "instrument": f"NIFTY FUT {expiry}",
                "source": "BREEZE",
            }
        )
    dedup = {row["timestamp"]: row for row in rows}
    ordered = sorted(dedup.values(), key=lambda row: row["timestamp"])
    return ordered, {
        "rejected_bad_timestamp": rejected_bad_timestamp,
        "rejected_outside_session": rejected_outside_session,
        "rejected_missing_fields": rejected_missing_fields,
        "duplicate_normalized_rows": len(rows) - len(ordered),
    }


def _day_is_complete(
    rows: list[dict[str, Any]],
    *,
    day: str,
    expiry: str,
) -> bool:
    if len(rows) != int(EXPECTED["one_minute_bars_per_session"]):
        return False
    if {str(row["timestamp"]) for row in rows} != _expected_timestamps(day):
        return False
    for row in rows:
        if str(row.get("expiry")) != expiry:
            return False
        if str(row.get("instrument")) != f"NIFTY FUT {expiry}":
            return False
        values = [row.get(name) for name in ("open", "high", "low", "close", "volume", "open_interest")]
        if any(value is None for value in values):
            return False
        low = float(row["low"])
        high = float(row["high"])
        if not (
            low <= float(row["open"]) <= high
            and low <= float(row["close"]) <= high
        ):
            return False
    return True


def _group_rows(
    rows: list[dict[str, Any]],
    session_dates: list[str],
) -> dict[str, list[dict[str, Any]]]:
    allowed = set(session_dates)
    grouped = {day: [] for day in session_dates}
    for row in rows:
        day = str(row.get("timestamp", ""))[:10]
        if day not in allowed:
            raise ValueError(f"intrabar row outside frozen Cohort-3 sessions: {day}")
        grouped[day].append(dict(row))
    for day in grouped:
        dedup = {str(row["timestamp"]): row for row in grouped[day]}
        grouped[day] = sorted(dedup.values(), key=lambda row: str(row["timestamp"]))
    return grouped


def _quality(
    grouped: dict[str, list[dict[str, Any]]],
    contract_by_date: dict[str, str],
    *,
    remaining_deficient: list[str],
) -> dict[str, Any]:
    ordered = [row for day in SESSION_DATES for row in grouped[day]]
    invalid_ohlc = 0
    wrong_contract = 0
    complete = 0
    for day in SESSION_DATES:
        expiry = contract_by_date[day]
        rows = grouped[day]
        if _day_is_complete(rows, day=day, expiry=expiry):
            complete += 1
        for row in rows:
            low = float(row["low"])
            high = float(row["high"])
            invalid_ohlc += not (
                low <= float(row["open"]) <= high
                and low <= float(row["close"]) <= high
            )
            wrong_contract += (
                str(row.get("expiry")) != expiry
                or str(row.get("instrument")) != f"NIFTY FUT {expiry}"
            )
    return {
        "sessions": len(SESSION_DATES),
        "rows": len(ordered),
        "duplicate_rows": 0,
        "invalid_ohlc_rows": int(invalid_ohlc),
        "wrong_contract_rows": int(wrong_contract),
        "complete_375_bar_sessions": int(complete),
        "rows_by_session": {day: len(grouped[day]) for day in SESSION_DATES},
        "failed_requests": len(remaining_deficient),
    }


def repair(
    market: dict[str, Any],
    existing: dict[str, Any],
    breeze: Any,
    *,
    attempts_per_session: int = 3,
    throttle_seconds: float = 0.5,
) -> dict[str, Any]:
    if attempts_per_session <= 0:
        raise ValueError("attempts_per_session must be positive")
    validate_market(market)
    if list(existing.get("session_dates") or []) != SESSION_DATES:
        raise ValueError("existing intrabar sessions differ from frozen Cohort 3")

    contract_by_date = _contract_by_date(market)
    grouped = _group_rows(list(existing.get("rows") or []), SESSION_DATES)
    initial_counts = {day: len(grouped[day]) for day in SESSION_DATES}
    deficient = [
        day
        for day in SESSION_DATES
        if not _day_is_complete(
            grouped[day], day=day, expiry=contract_by_date[day]
        )
    ]

    repair_diagnostics: list[dict[str, Any]] = []
    for day in deficient:
        expiry = contract_by_date[day]
        accepted: list[dict[str, Any]] | None = None
        for attempt in range(1, attempts_per_session + 1):
            response = breeze.get_historical_data_v2(
                interval="1minute",
                from_date=f"{day}T09:15:00.000Z",
                to_date=f"{day}T15:30:00.000Z",
                stock_code="NIFTY",
                exchange_code="NFO",
                product_type="futures",
                expiry_date=f"{expiry}T07:00:00.000Z",
                right="others",
                strike_price="0",
            )
            success = list((response or {}).get("Success") or [])
            normalized, rejected = _normalize_success(
                success, day=day, expiry=expiry
            )
            complete = _day_is_complete(
                normalized, day=day, expiry=expiry
            )
            repair_diagnostics.append(
                {
                    "date": day,
                    "expiry": expiry,
                    "attempt": attempt,
                    "status": (response or {}).get("Status"),
                    "error": (response or {}).get("Error"),
                    "raw_count": len(success),
                    "normalized_valid_count": len(normalized),
                    "complete_375": complete,
                    **rejected,
                }
            )
            if complete:
                accepted = normalized
                break
            time_module.sleep(max(0.0, throttle_seconds))
        if accepted is not None:
            grouped[day] = accepted
        time_module.sleep(max(0.0, throttle_seconds))

    remaining = [
        day
        for day in SESSION_DATES
        if not _day_is_complete(
            grouped[day], day=day, expiry=contract_by_date[day]
        )
    ]
    rows = [row for day in SESSION_DATES for row in grouped[day]]
    quality = _quality(
        grouped, contract_by_date, remaining_deficient=remaining
    )
    report = {
        "research_type": "NIFTY_FUTURES_INTRABAR_DEVELOPMENT_REPAIRED_V1",
        "research_only": True,
        "candidate_frozen": False,
        "blind_data_used": False,
        "implementation_allowed": False,
        "interval_minutes": 1,
        "session_dates": list(SESSION_DATES),
        "contract_by_date": contract_by_date,
        "rows": rows,
        "quality": quality,
        "repair": {
            "policy": (
                "refetch deficient frozen sessions individually; replace a session "
                "only with an exact valid 375-minute response; never fabricate rows"
            ),
            "initial_rows": sum(initial_counts.values()),
            "initial_rows_by_session": initial_counts,
            "initial_deficient_sessions": deficient,
            "attempts_per_session": attempts_per_session,
            "repaired_sessions": [
                day for day in deficient if day not in remaining
            ],
            "remaining_deficient_sessions": remaining,
            "diagnostics": repair_diagnostics,
        },
        "request_diagnostics": list(existing.get("request_diagnostics") or []),
    }
    if not remaining:
        validate_intrabar(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Repair deficient frozen Cohort-3 one-minute futures sessions"
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument("--existing", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--attempts-per-session", type=int, default=3)
    parser.add_argument("--throttle-seconds", type=float, default=0.5)
    args = parser.parse_args()

    market = json.loads(args.market.read_text(encoding="utf-8"))
    existing = json.loads(args.existing.read_text(encoding="utf-8"))

    _load_local_env()
    key, secret, token = (
        _secret("BREEZE_API_KEY"),
        _secret("BREEZE_SECRET_KEY"),
        _secret("BREEZE_SESSION_TOKEN"),
    )
    if not (key and secret and token):
        raise RuntimeError(
            "BREEZE_API_KEY, BREEZE_SECRET_KEY and BREEZE_SESSION_TOKEN are required"
        )
    from breeze_connect import BreezeConnect

    breeze = BreezeConnect(api_key=key)
    breeze.generate_session(api_secret=secret, session_token=token)
    report = repair(
        market,
        existing,
        breeze,
        attempts_per_session=args.attempts_per_session,
        throttle_seconds=args.throttle_seconds,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(json.dumps({
        "output": str(args.output),
        "initial_rows": report["repair"]["initial_rows"],
        "initial_deficient_sessions": report["repair"]["initial_deficient_sessions"],
        "repaired_sessions": report["repair"]["repaired_sessions"],
        "remaining_deficient_sessions": report["repair"]["remaining_deficient_sessions"],
        "quality": report["quality"],
    }, indent=2))
    if report["repair"]["remaining_deficient_sessions"]:
        raise RuntimeError(
            "Cohort-3 intrabar repair remains incomplete; inspect repair diagnostics "
            "in the output artifact. No missing minute bars were fabricated."
        )


if __name__ == "__main__":
    main()
