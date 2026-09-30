"""Collect the frozen 2022-2024 Breeze NIFTY futures replication artifact."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from services.historical.independent_older_market_structure_replication_protocol import (
    PROTOCOL_VERSION,
    WINDOW,
)
from services.historical.independent_prospective_magnitude_market import (
    _load_local_env,
    _secret,
)

IST = ZoneInfo("Asia/Kolkata")
RESEARCH_TYPE = "NIFTY_BREEZE_OLDER_MARKET_STRUCTURE_MARKET_V1"
MAX_CHUNK_DAYS = 10


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


def _chunks(start: date, end: date):
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=MAX_CHUNK_DAYS - 1))
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def _load_manifest(path: Path) -> tuple[dict[str, Any], dict[str, date]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("expiry manifest protocol version mismatch")
    if payload.get("provider") != "BREEZE":
        raise ValueError("expiry manifest must be BREEZE")
    if payload.get("complete") is not True:
        raise ValueError("expiry manifest is incomplete")
    contracts = list(payload.get("contracts") or [])
    if len(contracts) != 36:
        raise ValueError(f"expected 36 monthly contracts, got {len(contracts)}")
    mapping: dict[str, date] = {}
    for row in contracts:
        month = str(row.get("month") or "")
        expiry_raw = str(row.get("expiry") or "")
        if not month or not expiry_raw:
            raise ValueError("expiry manifest contains empty month/expiry")
        mapping[month] = date.fromisoformat(expiry_raw)
    expected_months = []
    cursor = date(2022, 1, 1)
    while cursor <= date(2024, 12, 1):
        expected_months.append(cursor.strftime("%Y-%m"))
        cursor = (
            date(cursor.year + 1, 1, 1)
            if cursor.month == 12
            else date(cursor.year, cursor.month + 1, 1)
        )
    if sorted(mapping) != expected_months:
        raise ValueError("expiry manifest months do not match frozen 2022-2024 window")
    return payload, mapping


def _contract_periods(mapping: dict[str, date]) -> list[tuple[date, date, date]]:
    expiries = [mapping[key] for key in sorted(mapping)]
    start = date.fromisoformat(WINDOW["start"])
    end = date.fromisoformat(WINDOW["end"])
    periods: list[tuple[date, date, date]] = []
    cursor = start
    for expiry in expiries:
        if cursor > end:
            break
        period_end = min(expiry, end)
        if cursor <= period_end:
            periods.append((cursor, period_end, expiry))
        cursor = expiry + timedelta(days=1)
    return periods


def _parse_ts(raw: Any) -> datetime:
    value = str(raw or "").strip()
    if not value:
        raise ValueError("missing datetime")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    else:
        parsed = parsed.astimezone(IST)
    return parsed.replace(second=0, microsecond=0)


def _num(row: dict[str, Any], key: str) -> float:
    value = float(row.get(key))
    if not (value > 0.0):
        raise ValueError(f"nonpositive {key}")
    return value


def _normalize_rows(
    response: dict[str, Any] | None,
    *,
    expiry: date,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in list((response or {}).get("Success") or []):
        try:
            ts = _parse_ts(item.get("datetime") or item.get("date"))
        except Exception:
            continue
        if not (start <= ts.date() <= end):
            continue
        if not (
            time(9, 15) <= ts.time() <= time(15, 25)
        ):
            continue
        open_px = _num(item, "open")
        high = _num(item, "high")
        low = _num(item, "low")
        close = _num(item, "close")
        if not (low <= open_px <= high and low <= close <= high):
            raise ValueError(f"invalid OHLC on {ts.isoformat()}")
        key = ts.isoformat()
        if key in out:
            raise ValueError(f"duplicate timestamp {key}")
        out[key] = {
            "timestamp": key,
            "date": ts.date().isoformat(),
            "open": open_px,
            "high": high,
            "low": low,
            "close": close,
            "instrument": f"NIFTY FUT {expiry.isoformat()}",
            "futures_expiry": expiry.isoformat(),
            "source": "BREEZE",
        }
    return [out[key] for key in sorted(out)]


def collect_market(
    client: Any,
    manifest_path: Path,
) -> dict[str, Any]:
    manifest, mapping = _load_manifest(manifest_path)
    rows: list[dict[str, Any]] = []
    request_diagnostics: list[dict[str, Any]] = []

    for period_start, period_end, expiry in _contract_periods(mapping):
        for chunk_start, chunk_end in _chunks(period_start, period_end):
            response = client.get_historical_data_v2(
                interval="5minute",
                from_date=f"{chunk_start.isoformat()}T09:15:00.000Z",
                to_date=f"{chunk_end.isoformat()}T15:30:00.000Z",
                stock_code="NIFTY",
                exchange_code="NFO",
                product_type="futures",
                expiry_date=f"{expiry.isoformat()}T06:00:00.000Z",
                right="others",
                strike_price="0",
            )
            normalized = _normalize_rows(
                response,
                expiry=expiry,
                start=chunk_start,
                end=chunk_end,
            )
            rows.extend(normalized)
            request_diagnostics.append(
                {
                    "expiry": expiry.isoformat(),
                    "start": chunk_start.isoformat(),
                    "end": chunk_end.isoformat(),
                    "status": (response or {}).get("Status"),
                    "error": (response or {}).get("Error"),
                    "accepted_rows": len(normalized),
                }
            )

    by_day: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_day.setdefault(str(row["date"]), []).append(row)

    complete: list[str] = []
    rejected: list[dict[str, Any]] = []
    accepted_rows: list[dict[str, Any]] = []
    for day in sorted(by_day):
        day_rows = sorted(by_day[day], key=lambda row: row["timestamp"])
        if len(day_rows) != int(WINDOW["bars_per_session"]):
            rejected.append({"date": day, "rows": len(day_rows)})
            continue
        expected = [
            (
                datetime.fromisoformat(f"{day}T09:15:00+05:30")
                + timedelta(minutes=5 * i)
            ).isoformat()
            for i in range(int(WINDOW["bars_per_session"]))
        ]
        actual = [str(row["timestamp"]) for row in day_rows]
        if actual != expected:
            rejected.append({"date": day, "rows": len(day_rows), "shape": "mismatch"})
            continue
        complete.append(day)
        accepted_rows.extend(day_rows)

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "research_only": True,
        "provider": "BREEZE",
        "window": WINDOW,
        "expiry_manifest_sha256": _sha256(manifest_path),
        "expiry_contracts": {
            month: expiry.isoformat() for month, expiry in sorted(mapping.items())
        },
        "qa": {
            "complete_sessions": len(complete),
            "complete_session_dates": complete,
            "rejected_sessions": len(rejected),
            "rejections": rejected,
            "accepted_rows": len(accepted_rows),
        },
        "request_diagnostics": request_diagnostics,
        "futures_rows": accepted_rows,
        "pattern_scoring_performed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect frozen 2022-2024 Breeze NIFTY futures replication market"
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/independent_older_market_structure_market.json"),
    )
    args = parser.parse_args()
    _load_local_env()
    report = collect_market(_breeze_client(), args.manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "output": str(args.output),
                "sha256": digest,
                "complete_sessions": report["qa"]["complete_sessions"],
                "rejected_sessions": report["qa"]["rejected_sessions"],
                "accepted_rows": report["qa"]["accepted_rows"],
                "pattern_scoring_performed": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
