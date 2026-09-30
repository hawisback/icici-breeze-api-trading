"""Discover exact 2022-2024 monthly NIFTY futures expiries from Breeze history.

This is provenance-only contract discovery. It stores dates, row counts and
request diagnostics, not OHLC values and not research outcomes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time as time_module
from calendar import monthrange
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from services.historical.independent_older_market_structure_replication_protocol import (
    CONTRACT_DISCOVERY,
    PROTOCOL_VERSION,
    WINDOW,
)
from services.historical.independent_prospective_magnitude_market import (
    _load_local_env,
    _secret,
)

RESEARCH_TYPE = "NIFTY_BREEZE_OLDER_EXPIRY_MANIFEST_V1"
PROBE_SLEEP_SECONDS = 0.30


def _month_iter(start: date, end: date):
    cursor = date(start.year, start.month, 1)
    while cursor <= end:
        yield cursor.year, cursor.month
        cursor = (
            date(cursor.year + 1, 1, 1)
            if cursor.month == 12
            else date(cursor.year, cursor.month + 1, 1)
        )


def _expiry_candidates(year: int, month: int) -> list[date]:
    last = date(year, month, monthrange(year, month)[1])
    nominal = last - timedelta(days=(last.weekday() - 3) % 7)
    values: list[date] = []
    cursor = nominal
    while len(values) < int(CONTRACT_DISCOVERY["candidate_business_days_back"]):
        if cursor.weekday() < 5:
            values.append(cursor)
        cursor -= timedelta(days=1)
    return values


def _breeze_client():
    key = _secret("BREEZE_API_KEY")
    secret = _secret("BREEZE_SECRET_KEY")
    token = _secret("BREEZE_SESSION_TOKEN")
    if not (key and secret and token):
        raise RuntimeError(
            "BREEZE_API_KEY, BREEZE_SECRET_KEY and BREEZE_SESSION_TOKEN are required"
        )
    from breeze_connect import BreezeConnect

    breeze = BreezeConnect(api_key=key)
    breeze.generate_session(api_secret=secret, session_token=token)
    return breeze


def _probe_candidate(client: Any, expiry: date) -> dict[str, Any]:
    probe_start = expiry - timedelta(
        days=int(CONTRACT_DISCOVERY["probe_calendar_days_back"])
    )
    response = client.get_historical_data_v2(
        interval="5minute",
        from_date=f"{probe_start.isoformat()}T09:15:00.000Z",
        to_date=f"{expiry.isoformat()}T15:30:00.000Z",
        stock_code="NIFTY",
        exchange_code="NFO",
        product_type="futures",
        expiry_date=f"{expiry.isoformat()}T06:00:00.000Z",
        right="others",
        strike_price="0",
    )
    rows = list((response or {}).get("Success") or [])
    dates: set[str] = set()
    for row in rows:
        raw = str(row.get("datetime") or row.get("date") or "")
        if not raw:
            continue
        try:
            ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        dates.add(ts.date().isoformat())
    return {
        "expiry": expiry.isoformat(),
        "status": (response or {}).get("Status"),
        "error": (response or {}).get("Error"),
        "rows": len(rows),
        "returned_dates": sorted(dates),
    }


def discover_manifest(
    client: Any,
    *,
    sleep_seconds: float = PROBE_SLEEP_SECONDS,
) -> dict[str, Any]:
    start = date.fromisoformat(WINDOW["start"])
    end = date.fromisoformat(WINDOW["end"])
    discovery_end = date(end.year + 1, 1, 1)
    months: list[dict[str, Any]] = []

    for year, month in _month_iter(start, discovery_end):
        attempts: list[dict[str, Any]] = []
        resolved: str | None = None
        for candidate in _expiry_candidates(year, month):
            result = _probe_candidate(client, candidate)
            attempts.append(result)
            if int(result["rows"]) >= int(CONTRACT_DISCOVERY["minimum_probe_rows"]):
                resolved = candidate.isoformat()
                break
            if sleep_seconds > 0:
                time_module.sleep(sleep_seconds)

        months.append(
            {
                "month": f"{year:04d}-{month:02d}",
                "resolved_expiry": resolved,
                "attempts": attempts,
            }
        )

    unresolved = [row["month"] for row in months if row["resolved_expiry"] is None]
    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "research_only": True,
        "provider": "BREEZE",
        "window": [WINDOW["start"], WINDOW["end"]],
        "months_expected": len(months),
        "months_resolved": len(months) - len(unresolved),
        "unresolved_months": unresolved,
        "complete": not unresolved,
        "contracts": [
            {
                "month": row["month"],
                "expiry": row["resolved_expiry"],
                "probe_rows": (
                    next(
                        attempt["rows"]
                        for attempt in row["attempts"]
                        if attempt["expiry"] == row["resolved_expiry"]
                    )
                    if row["resolved_expiry"]
                    else 0
                ),
            }
            for row in months
        ],
        "probe_diagnostics": months,
        "outcome_values_stored": False,
        "pattern_scoring_performed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover 2022-2024 NIFTY monthly futures expiries from Breeze"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/independent_older_nifty_expiry_manifest.json"),
    )
    args = parser.parse_args()
    _load_local_env()
    report = discover_manifest(_breeze_client())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "output": str(args.output),
                "sha256": digest,
                "months_expected": report["months_expected"],
                "months_resolved": report["months_resolved"],
                "unresolved_months": report["unresolved_months"],
                "complete": report["complete"],
            },
            indent=2,
        )
    )
    if not report["complete"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
