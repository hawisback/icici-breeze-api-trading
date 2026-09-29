"""Collect 1-minute canonical NIFTY futures for development lead-timing research.

Contract identity is derived from the already-frozen development underlying rows,
so this collector cannot silently substitute a different futures contract.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time as time_module
from collections import defaultdict
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
SESSION_START = time(9, 15)
SESSION_END = time(15, 30)
FUTURES_RE = re.compile(r"^NIFTY FUT (\d{4}-\d{2}-\d{2})$")


def _load_local_env(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _secret(name: str) -> str | None:
    value = os.getenv(name)
    if not value or "your_" in value.lower() or "change_me" in value.lower():
        return None
    return value


def _number(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _contract_by_date(payload: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, set[str]] = defaultdict(set)
    source_rows = list(
        payload.get("underlying_market_rows")
        or payload.get("canonical_market_rows")
        or []
    )
    if not source_rows:
        raise ValueError(
            "market payload must contain underlying_market_rows or canonical_market_rows"
        )
    for row in source_rows:
        day = str(row["timestamp"])[:10]
        match = FUTURES_RE.match(str(row["futures_instrument"]))
        if not match:
            raise ValueError(f"unexpected futures instrument: {row['futures_instrument']}")
        mapping[day].add(match.group(1))
    result: dict[str, str] = {}
    for day in payload["session_dates"]:
        expiries = mapping.get(day, set())
        if len(expiries) != 1:
            raise ValueError(f"{day} has {len(expiries)} canonical futures expiries")
        result[day] = next(iter(expiries))
    return result


def _pairs(values: list[str]) -> list[list[str]]:
    return [values[index : index + 2] for index in range(0, len(values), 2)]


def collect(payload: dict[str, Any], throttle_seconds: float = 0.35) -> dict[str, Any]:
    _load_local_env()
    key, secret, token = (_secret("BREEZE_API_KEY"), _secret("BREEZE_SECRET_KEY"), _secret("BREEZE_SESSION_TOKEN"))
    if not (key and secret and token):
        raise RuntimeError("BREEZE_API_KEY, BREEZE_SECRET_KEY and BREEZE_SESSION_TOKEN are required")

    from breeze_connect import BreezeConnect

    breeze = BreezeConnect(api_key=key)
    breeze.generate_session(api_secret=secret, session_token=token)

    contract_by_date = _contract_by_date(payload)
    by_expiry: dict[str, list[str]] = defaultdict(list)
    for day in payload["session_dates"]:
        by_expiry[contract_by_date[day]].append(day)

    rows: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for expiry, days in sorted(by_expiry.items()):
        for chunk in _pairs(days):
            first, last = chunk[0], chunk[-1]
            response = breeze.get_historical_data_v2(
                interval="1minute",
                from_date=f"{first}T09:15:00.000Z",
                to_date=f"{last}T15:30:00.000Z",
                stock_code="NIFTY",
                exchange_code="NFO",
                product_type="futures",
                expiry_date=f"{expiry}T07:00:00.000Z",
                right="others",
                strike_price="0",
            )
            success = list((response or {}).get("Success") or [])
            diagnostics.append(
                {
                    "expiry": expiry,
                    "start": first,
                    "end": last,
                    "status": (response or {}).get("Status"),
                    "error": (response or {}).get("Error"),
                    "raw_count": len(success),
                }
            )
            wanted = set(chunk)
            for item in success:
                try:
                    ts = datetime.fromisoformat(str(item.get("datetime", "")))
                except ValueError:
                    continue
                ts = ts.replace(tzinfo=IST) if ts.tzinfo is None else ts.astimezone(IST)
                ts = ts.replace(second=0, microsecond=0)
                if ts.date().isoformat() not in wanted or not (SESSION_START <= ts.time() < SESSION_END):
                    continue
                values = [_number(item.get(name)) for name in ("open", "high", "low", "close", "volume", "open_interest")]
                if any(value is None for value in values):
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
            time_module.sleep(max(0.0, throttle_seconds))

    dedup = {row["timestamp"]: row for row in rows}
    ordered = sorted(dedup.values(), key=lambda row: row["timestamp"])
    counts = {day: 0 for day in payload["session_dates"]}
    invalid_ohlc = 0
    wrong_contract = 0
    for row in ordered:
        day = str(row["timestamp"])[:10]
        if day in counts:
            counts[day] += 1
            wrong_contract += row["expiry"] != contract_by_date[day]
        invalid_ohlc += not (
            row["low"] <= row["open"] <= row["high"]
            and row["low"] <= row["close"] <= row["high"]
        )
    return {
        "research_type": "NIFTY_FUTURES_INTRABAR_DEVELOPMENT_V1",
        "research_only": True,
        "interval_minutes": 1,
        "session_dates": list(payload["session_dates"]),
        "contract_by_date": contract_by_date,
        "rows": ordered,
        "quality": {
            "sessions": len(payload["session_dates"]),
            "rows": len(ordered),
            "duplicate_rows": len(rows) - len(ordered),
            "invalid_ohlc_rows": int(invalid_ohlc),
            "wrong_contract_rows": int(wrong_contract),
            "complete_375_bar_sessions": sum(value == 375 for value in counts.values()),
            "rows_by_session": counts,
            "failed_requests": sum(
                bool(item.get("error")) or item.get("status") not in (None, 200)
                for item in diagnostics
            ),
        },
        "request_diagnostics": diagnostics,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect 1-minute development NIFTY futures")
    parser.add_argument("--session-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--throttle-seconds", type=float, default=0.35)
    args = parser.parse_args()
    payload = json.loads(args.session_source.read_text(encoding="utf-8"))
    report = collect(payload, args.throttle_seconds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "quality": report["quality"]}, indent=2))


if __name__ == "__main__":
    main()
