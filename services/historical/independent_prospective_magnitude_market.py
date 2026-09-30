"""Collect Breeze futures data for the frozen prospective magnitude replication.

This collector is deliberately all-or-nothing. It refuses to call Breeze until
all 30 frozen sessions have completed, preventing incremental inspection of the
prospective sample. It uses only the frozen contract-by-date roll schedule.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from services.historical.independent_prospective_magnitude_protocol import (
    COLLECTION_AND_SCORING_POLICY,
    CONTRACT_BY_DATE,
    EXPECTED_BARS_PER_SESSION,
    EXPECTED_ROWS,
    PROTOCOL_VERSION,
    SESSION_DATES,
    SESSION_END_EXCLUSIVE,
    SESSION_START,
)

IST = ZoneInfo("Asia/Kolkata")
RESEARCH_TYPE = "NIFTY_PROSPECTIVE_MAGNITUDE_MARKET_DATA_V1"


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
    if not value:
        return None
    lowered = value.lower()
    if "your_" in lowered or "change_me" in lowered:
        return None
    return value


def _num(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _wall(day: str, hhmm: str) -> str:
    return f"{day}T{hhmm}:00.000Z"


def _expected_timestamps(day: str) -> list[str]:
    start = datetime.fromisoformat(f"{day}T{SESSION_START}:00+05:30")
    return [
        (start + timedelta(minutes=5 * i)).isoformat()
        for i in range(EXPECTED_BARS_PER_SESSION)
    ]


def _full_sample_completion() -> datetime:
    return datetime.fromisoformat(
        COLLECTION_AND_SCORING_POLICY["full_sample_not_complete_before"]
    ).astimezone(IST)


def assert_collection_window_open(now: datetime | None = None) -> None:
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    else:
        current = current.astimezone(IST)
    if current < _full_sample_completion():
        raise RuntimeError(
            "Prospective collection is locked until the full frozen sample has "
            f"completed at {_full_sample_completion().isoformat()}."
        )


def collect(*, now: datetime | None = None) -> dict[str, Any]:
    assert_collection_window_open(now)

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

    all_rows: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []

    for day in SESSION_DATES:
        expiry = CONTRACT_BY_DATE[day]
        response = breeze.get_historical_data_v2(
            interval="5minute",
            from_date=_wall(day, SESSION_START),
            to_date=_wall(day, SESSION_END_EXCLUSIVE),
            stock_code="NIFTY",
            exchange_code="NFO",
            product_type="futures",
            expiry_date=f"{expiry}T07:00:00.000Z",
            right="others",
            strike_price="0",
        )
        success = list((response or {}).get("Success") or [])
        normalized: dict[str, dict[str, Any]] = {}

        for item in success:
            try:
                ts = datetime.fromisoformat(str(item.get("datetime", "")))
            except ValueError:
                continue
            ts = ts.replace(tzinfo=IST) if ts.tzinfo is None else ts.astimezone(IST)
            ts = ts.replace(second=0, microsecond=0)
            if ts.date().isoformat() != day:
                continue
            if not (
                time.fromisoformat(SESSION_START)
                <= ts.time()
                < time.fromisoformat(SESSION_END_EXCLUSIVE)
            ):
                continue

            values = {
                field: _num(item.get(field))
                for field in (
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "open_interest",
                )
            }
            if any(value is None for value in values.values()):
                raise ValueError(f"{day} {ts.isoformat()} missing required field")
            if not (
                values["low"] <= values["open"] <= values["high"]
                and values["low"] <= values["close"] <= values["high"]
            ):
                raise ValueError(f"{day} {ts.isoformat()} invalid OHLC")
            if values["volume"] <= 0.0:
                raise ValueError(f"{day} {ts.isoformat()} nonpositive volume")
            if values["open_interest"] <= 0.0:
                raise ValueError(f"{day} {ts.isoformat()} nonpositive OI")

            key_ts = ts.isoformat()
            if key_ts in normalized:
                raise ValueError(f"{day} duplicate timestamp {key_ts}")
            normalized[key_ts] = {
                "timestamp": key_ts,
                **values,
                "instrument": f"NIFTY FUT {expiry}",
                "futures_expiry": expiry,
                "source": "BREEZE",
            }

        ordered = [normalized[k] for k in sorted(normalized)]
        expected = _expected_timestamps(day)
        actual = [row["timestamp"] for row in ordered]
        diagnostics.append(
            {
                "date": day,
                "futures_expiry": expiry,
                "status": (response or {}).get("Status"),
                "error": (response or {}).get("Error"),
                "raw_count": len(success),
                "accepted_count": len(ordered),
            }
        )
        if actual != expected:
            raise ValueError(
                f"{day} exact prospective shape mismatch: "
                f"expected {len(expected)} bars, got {len(actual)}"
            )
        all_rows.extend(ordered)

    if len(all_rows) != EXPECTED_ROWS:
        raise ValueError(f"expected {EXPECTED_ROWS} rows, got {len(all_rows)}")

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "research_only": True,
        "prospective_sample": True,
        "candidate_frozen": False,
        "blind_validation_opened": False,
        "implementation_allowed": False,
        "provider": "BREEZE",
        "session_dates": list(SESSION_DATES),
        "contract_by_date": dict(CONTRACT_BY_DATE),
        "session_start": SESSION_START,
        "session_end_exclusive": SESSION_END_EXCLUSIVE,
        "bars_per_session": EXPECTED_BARS_PER_SESSION,
        "rows": len(all_rows),
        "quality": {
            "sessions": len(SESSION_DATES),
            "rows": len(all_rows),
            "complete_77_bar_sessions": len(SESSION_DATES),
            "duplicate_rows": 0,
            "invalid_ohlc_rows": 0,
            "missing_volume_rows": 0,
            "missing_open_interest_rows": 0,
            "nonpositive_volume_rows": 0,
            "nonpositive_open_interest_rows": 0,
            "wrong_contract_rows": 0,
        },
        "request_diagnostics": diagnostics,
        "futures_rows": all_rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect frozen prospective NIFTY magnitude replication data"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/independent_prospective_magnitude_market.json"),
    )
    args = parser.parse_args()
    _load_local_env()
    report = collect()
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
                "sessions": len(report["session_dates"]),
                "rows": report["rows"],
                "quality": report["quality"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
