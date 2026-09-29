"""One-minute reconciliation for the isolated Cohort-4 Breeze volume defect.

This is data QA, not strategy research. The exact repaired-input artifact and
defective 5-minute row are frozen before the 1-minute data are fetched.

A correction is permitted only if all of the following hold:
1. The frozen repaired-input SHA256 matches exactly.
2. Breeze returns the exact 375 regular one-minute bars for 2025-06-27 on the
   frozen 2025-07-31 NIFTY futures contract.
3. Every one-minute OHLC row is valid and every one-minute volume/OI is strictly
   positive.
4. Aggregating those 375 rows into 75 five-minute bars reproduces all 75 stored
   five-minute OHLC bars for the session.
5. For the other 74 five-minute bars, aggregated one-minute volume equals the
   stored positive five-minute volume.
6. At 14:30, the aggregated one-minute volume is strictly positive and equals
   abs(the stored defective -826950 volume), establishing an isolated sign
   defect rather than an arbitrary replacement.

Only then may the 14:30 five-minute volume be replaced by the one-minute sum.
No abs(), interpolation, forward-fill, contract substitution, or price/OI edit
is allowed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from services.historical.independent_cohort4_protocol import validate_market
from services.historical.independent_futures_intrabar_research import (
    _load_local_env,
    _secret,
)

IST = ZoneInfo("Asia/Kolkata")

INPUT_SHA256 = "e87ae99cd56d26e3ddd39469f9cda32cfa7efc60ac9802c3e62d99a8d11a9803"
TARGET_DAY = "2025-06-27"
TARGET_EXPIRY = "2025-07-31"
TARGET_TIMESTAMP = "2025-06-27T14:30:00+05:30"
TARGET_STORED_VOLUME = -826950.0
EXPECTED_SESSION_ONE_MINUTE_ROWS = 375
EXPECTED_SESSION_FIVE_MINUTE_ROWS = 75
OHLC_TOLERANCE = 1e-9
VOLUME_TOLERANCE = 1e-9

RESEARCH_TYPE = "NIFTY_DEVELOPMENT_COHORT_4_MARKET_DATA_RECONCILED_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _number(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _expected_one_minute_timestamps() -> list[str]:
    start = datetime.fromisoformat(f"{TARGET_DAY}T09:15:00+05:30")
    return [
        (start + timedelta(minutes=index)).isoformat()
        for index in range(EXPECTED_SESSION_ONE_MINUTE_ROWS)
    ]


def _normalize_one_minute_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wanted = set(_expected_one_minute_timestamps())
    instrument = f"NIFTY FUT {TARGET_EXPIRY}"
    normalized: dict[str, dict[str, Any]] = {}

    for raw in rows:
        try:
            ts = datetime.fromisoformat(str(raw.get("timestamp", "")))
        except ValueError as exc:
            raise ValueError("1-minute reconciliation contains bad timestamp") from exc
        ts = ts.replace(tzinfo=IST) if ts.tzinfo is None else ts.astimezone(IST)
        ts = ts.replace(second=0, microsecond=0)
        ts_text = ts.isoformat()
        if ts_text not in wanted:
            continue

        values = {
            name: _number(raw.get(name))
            for name in ("open", "high", "low", "close", "volume", "open_interest")
        }
        if any(value is None for value in values.values()):
            raise ValueError(f"1-minute row {ts_text} has missing required field")
        if not (
            values["low"] <= values["open"] <= values["high"]
            and values["low"] <= values["close"] <= values["high"]
        ):
            raise ValueError(f"1-minute row {ts_text} has invalid OHLC")
        if values["volume"] <= 0.0:
            raise ValueError(f"1-minute row {ts_text} has nonpositive volume")
        if values["open_interest"] <= 0.0:
            raise ValueError(f"1-minute row {ts_text} has nonpositive OI")
        if str(raw.get("instrument")) != instrument:
            raise ValueError(
                f"1-minute row {ts_text} has wrong contract {raw.get('instrument')}"
            )
        if ts_text in normalized:
            raise ValueError(f"duplicate 1-minute timestamp {ts_text}")

        normalized[ts_text] = {
            "timestamp": ts_text,
            **values,
            "instrument": instrument,
            "source": "BREEZE",
        }

    ordered = [normalized[key] for key in sorted(normalized)]
    if [row["timestamp"] for row in ordered] != _expected_one_minute_timestamps():
        raise ValueError(
            f"expected exact {EXPECTED_SESSION_ONE_MINUTE_ROWS} one-minute bars, "
            f"got {len(ordered)}"
        )
    return ordered


def _aggregate_five_minute(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(rows) != EXPECTED_SESSION_ONE_MINUTE_ROWS:
        raise ValueError("cannot aggregate incomplete one-minute session")
    result: list[dict[str, Any]] = []
    for index in range(0, len(rows), 5):
        chunk = rows[index : index + 5]
        if len(chunk) != 5:
            raise ValueError("incomplete five-minute chunk")
        start = datetime.fromisoformat(chunk[0]["timestamp"])
        expected = [
            (start + timedelta(minutes=offset)).isoformat()
            for offset in range(5)
        ]
        if [row["timestamp"] for row in chunk] != expected:
            raise ValueError("non-contiguous one-minute chunk")
        result.append({
            "timestamp": chunk[0]["timestamp"],
            "open": chunk[0]["open"],
            "high": max(row["high"] for row in chunk),
            "low": min(row["low"] for row in chunk),
            "close": chunk[-1]["close"],
            "volume": sum(row["volume"] for row in chunk),
            "open_interest": chunk[-1]["open_interest"],
        })
    if len(result) != EXPECTED_SESSION_FIVE_MINUTE_ROWS:
        raise ValueError("expected 75 aggregated five-minute rows")
    return result


def _stored_session_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        row
        for row in list(payload.get("nifty_futures") or [])
        if str(row.get("timestamp", ""))[:10] == TARGET_DAY
    ]
    rows.sort(key=lambda row: str(row["timestamp"]))
    if len(rows) != EXPECTED_SESSION_FIVE_MINUTE_ROWS:
        raise ValueError("stored target session does not have 75 five-minute rows")
    return rows


def _close(left: float, right: float, tolerance: float) -> bool:
    return abs(float(left) - float(right)) <= tolerance


def reconcile_payload(
    payload: dict[str, Any],
    one_minute_rows: list[dict[str, Any]],
    *,
    input_sha256: str,
) -> dict[str, Any]:
    if input_sha256 != INPUT_SHA256:
        raise ValueError(
            f"repaired-input SHA changed: {input_sha256} != {INPUT_SHA256}"
        )
    if payload.get("research_type") != "NIFTY_DEVELOPMENT_COHORT_4_MARKET_DATA_REPAIRED_V1":
        raise ValueError("unexpected repaired-input research_type")
    if payload.get("blind_data_used") is not False:
        raise ValueError("Cohort-4 reconciliation must remain non-blind")
    if payload.get("implementation_allowed") is not False:
        raise ValueError("Cohort-4 reconciliation must remain research-only")

    normalized = _normalize_one_minute_rows(one_minute_rows)
    aggregated = _aggregate_five_minute(normalized)
    stored = _stored_session_rows(payload)
    aggregate_by_ts = {row["timestamp"]: row for row in aggregated}

    comparison_rows: list[dict[str, Any]] = []
    target_aggregate_volume: float | None = None
    for row in stored:
        ts = str(row["timestamp"])
        aggregate = aggregate_by_ts.get(ts)
        if aggregate is None:
            raise ValueError(f"missing aggregated five-minute row {ts}")
        for field in ("open", "high", "low", "close"):
            if not _close(row[field], aggregate[field], OHLC_TOLERANCE):
                raise ValueError(
                    f"{ts} aggregated 1m {field}={aggregate[field]} does not match "
                    f"stored 5m {field}={row[field]}"
                )

        stored_volume = float(row["volume"])
        aggregate_volume = float(aggregate["volume"])
        if ts == TARGET_TIMESTAMP:
            if stored_volume != TARGET_STORED_VOLUME:
                raise ValueError(
                    f"target stored volume changed: {stored_volume} != "
                    f"{TARGET_STORED_VOLUME}"
                )
            if aggregate_volume <= 0.0:
                raise ValueError("target aggregated one-minute volume is not positive")
            if not _close(
                aggregate_volume,
                abs(TARGET_STORED_VOLUME),
                VOLUME_TOLERANCE,
            ):
                raise ValueError(
                    "target aggregated one-minute volume does not equal absolute "
                    "value of stored defective five-minute volume"
                )
            target_aggregate_volume = aggregate_volume
        else:
            if stored_volume <= 0.0:
                raise ValueError(f"unexpected additional nonpositive 5m volume at {ts}")
            if not _close(stored_volume, aggregate_volume, VOLUME_TOLERANCE):
                raise ValueError(
                    f"{ts} aggregated 1m volume={aggregate_volume} does not match "
                    f"stored 5m volume={stored_volume}"
                )
        comparison_rows.append({
            "timestamp": ts,
            "stored_volume": stored_volume,
            "aggregated_one_minute_volume": aggregate_volume,
            "ohlc_match": True,
            "volume_match_or_target_abs_match": True,
        })

    if target_aggregate_volume is None:
        raise ValueError("target five-minute bar was not found")

    report = json.loads(json.dumps(payload))
    containers = [
        report.get("nifty_futures") or [],
        ((report.get("provider_series") or {}).get("BREEZE") or {}).get(
            "NIFTY_FUTURES"
        ) or [],
    ]
    for rows in containers:
        matches = [
            row for row in rows
            if str(row.get("timestamp")) == TARGET_TIMESTAMP
        ]
        if len(matches) != 1:
            raise ValueError("expected exactly one target row in futures representation")
        matches[0]["volume"] = target_aggregate_volume

    canonical_matches = [
        row for row in report.get("canonical_market_rows") or []
        if str(row.get("timestamp")) == TARGET_TIMESTAMP
    ]
    if len(canonical_matches) != 1:
        raise ValueError("expected exactly one target canonical row")
    canonical_matches[0]["futures_volume"] = target_aggregate_volume

    quality = report.get("quality") or {}
    quality["nonpositive_volume_rows"] = 0
    provider_quality = (
        ((report.get("provider_quality") or {}).get("nifty_futures") or {})
        .get("providers", {})
        .get("BREEZE")
    )
    if not isinstance(provider_quality, dict):
        raise ValueError("missing Breeze provider quality block")
    provider_quality["nonpositive_volume_rows"] = 0

    report["research_type"] = RESEARCH_TYPE
    report["reconciliation"] = {
        "input_sha256": input_sha256,
        "target_day": TARGET_DAY,
        "target_expiry": TARGET_EXPIRY,
        "target_timestamp": TARGET_TIMESTAMP,
        "stored_defective_volume": TARGET_STORED_VOLUME,
        "replacement_volume": target_aggregate_volume,
        "one_minute_rows": len(normalized),
        "aggregated_five_minute_rows": len(aggregated),
        "all_session_ohlc_matches": True,
        "all_other_session_volume_matches": True,
        "target_volume_equals_abs_defective_volume": True,
        "comparison_rows": comparison_rows,
        "policy": {
            "uses_same_frozen_contract": True,
            "uses_lower_granularity_provider_data": True,
            "only_target_volume_changed": True,
            "price_fields_changed": False,
            "open_interest_changed": False,
            "abs_applied_as_data_transform": False,
            "synthetic_fill": False,
            "interpolation": False,
            "forward_fill": False,
        },
    }
    validate_market(report)
    return report


def _fetch_one_minute_session() -> tuple[list[dict[str, Any]], dict[str, Any]]:
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
    response = breeze.get_historical_data_v2(
        interval="1minute",
        from_date=f"{TARGET_DAY}T09:15:00.000Z",
        to_date=f"{TARGET_DAY}T15:30:00.000Z",
        stock_code="NIFTY",
        exchange_code="NFO",
        product_type="futures",
        expiry_date=f"{TARGET_EXPIRY}T07:00:00.000Z",
        right="others",
        strike_price="0",
    )
    success = list((response or {}).get("Success") or [])
    rows: list[dict[str, Any]] = []
    instrument = f"NIFTY FUT {TARGET_EXPIRY}"
    for item in success:
        try:
            ts = datetime.fromisoformat(str(item.get("datetime", "")))
        except ValueError:
            continue
        ts = ts.replace(tzinfo=IST) if ts.tzinfo is None else ts.astimezone(IST)
        ts = ts.replace(second=0, microsecond=0)
        rows.append({
            "timestamp": ts.isoformat(),
            "open": _number(item.get("open")),
            "high": _number(item.get("high")),
            "low": _number(item.get("low")),
            "close": _number(item.get("close")),
            "volume": _number(item.get("volume")),
            "open_interest": _number(item.get("open_interest")),
            "instrument": instrument,
            "source": "BREEZE",
        })
    return rows, {
        "status": (response or {}).get("Status"),
        "error": (response or {}).get("Error"),
        "raw_count": len(success),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reconcile isolated Cohort-4 five-minute volume defect from 1m data"
    )
    parser.add_argument("--existing", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    input_sha = _sha256(args.existing)
    if input_sha != INPUT_SHA256:
        raise ValueError(f"repaired-input SHA mismatch: {input_sha} != {INPUT_SHA256}")
    payload = json.loads(args.existing.read_text(encoding="utf-8"))

    _load_local_env()
    rows, provider_diagnostics = _fetch_one_minute_session()

    try:
        report = reconcile_payload(
            payload,
            rows,
            input_sha256=input_sha,
        )
    except Exception as exc:
        diagnostics = {
            "research_type": "NIFTY_DEVELOPMENT_COHORT_4_VOLUME_RECONCILIATION_FAILED_V1",
            "research_only": True,
            "candidate_frozen": False,
            "blind_data_used": False,
            "implementation_allowed": False,
            "input_sha256": input_sha,
            "target_day": TARGET_DAY,
            "target_expiry": TARGET_EXPIRY,
            "target_timestamp": TARGET_TIMESTAMP,
            "provider_diagnostics": provider_diagnostics,
            "one_minute_rows_returned": len(rows),
            "failure": f"{type(exc).__name__}: {exc}",
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(diagnostics, indent=2, allow_nan=False),
            encoding="utf-8",
        )
        raise

    report["reconciliation"]["provider_diagnostics"] = provider_diagnostics
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(json.dumps({
        "output": str(args.output),
        "validation": "PASSED",
        "input_sha256": input_sha,
        "replacement_volume": report["reconciliation"]["replacement_volume"],
        "quality": report["quality"],
    }, indent=2))


if __name__ == "__main__":
    main()
