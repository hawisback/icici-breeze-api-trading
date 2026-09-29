"""Independent Upstox verification of the isolated Cohort-4 Breeze volume defect.

This module is evidence collection only. It never mutates the Cohort-4 market
artifact.

Frozen target:
- input repaired market SHA256:
  e87ae99cd56d26e3ddd39469f9cda32cfa7efc60ac9802c3e62d99a8d11a9803
- underlying: NSE_INDEX|Nifty 50
- expired futures expiry: 2025-07-31
- session: 2025-06-27
- target five-minute candle: 14:30 IST

Upstox expired-instrument APIs are used to resolve the expired futures contract
from the underlying+expiry and fetch exact 5-minute and 1-minute historical
candles for the session.

Evidence is accepted only when:
1. exactly one NIFTY FUT contract resolves for the frozen expiry;
2. the session contains exact regular-session shapes: 75 five-minute and
   375 one-minute bars;
3. the 14:30 Upstox five-minute OHLC exactly matches the stored Breeze OHLC;
4. the five 14:30..14:34 Upstox one-minute candles aggregate exactly to the
   Upstox 14:30 five-minute OHLC and volume;
5. all five target one-minute volumes and the target five-minute volume are
   strictly positive.

This script records evidence only. A later, separately frozen rule is required
before any Cohort-4 value may be changed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

IST = ZoneInfo("Asia/Kolkata")

INPUT_SHA256 = "e87ae99cd56d26e3ddd39469f9cda32cfa7efc60ac9802c3e62d99a8d11a9803"
UNDERLYING_KEY = "NSE_INDEX|Nifty 50"
TARGET_EXPIRY = "2025-07-31"
TARGET_DAY = "2025-06-27"
TARGET_TIMESTAMP = "2025-06-27T14:30:00+05:30"
EXPECTED_5M_ROWS = 75
EXPECTED_1M_ROWS = 375
BREEZE_TARGET_OHLC = {
    "open": 25733.9,
    "high": 25740.0,
    "low": 25733.9,
    "close": 25734.5,
}
OHLC_TOLERANCE = 1e-9
VOLUME_TOLERANCE = 1e-9

RESEARCH_TYPE = "NIFTY_DEVELOPMENT_COHORT_4_UPSTOX_VOLUME_VERIFICATION_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_local_env(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(
            key.strip(), value.strip().strip('"').strip("'")
        )


def _float(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _close(left: float, right: float, tolerance: float = OHLC_TOLERANCE) -> bool:
    return abs(float(left) - float(right)) <= tolerance


def _normalize_candles(
    raw: list[list[Any]],
    *,
    interval_minutes: int,
) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for candle in raw:
        if len(candle) < 6:
            continue
        try:
            ts = datetime.fromisoformat(str(candle[0]).replace("Z", "+00:00"))
        except ValueError:
            continue
        ts = ts.replace(tzinfo=IST) if ts.tzinfo is None else ts.astimezone(IST)
        ts = ts.replace(second=0, microsecond=0)
        if ts.date().isoformat() != TARGET_DAY:
            continue
        if ts.time() < datetime.strptime("09:15", "%H:%M").time():
            continue
        max_time = (
            datetime.strptime("15:25", "%H:%M").time()
            if interval_minutes == 5
            else datetime.strptime("15:29", "%H:%M").time()
        )
        if ts.time() > max_time:
            continue

        values = [_float(value) for value in candle[1:7]]
        if any(value is None for value in values[:5]):
            continue
        row = {
            "timestamp": ts.isoformat(),
            "open": values[0],
            "high": values[1],
            "low": values[2],
            "close": values[3],
            "volume": values[4],
            "open_interest": values[5],
        }
        if not (
            row["low"] <= row["open"] <= row["high"]
            and row["low"] <= row["close"] <= row["high"]
        ):
            raise ValueError(f"Upstox invalid OHLC at {row['timestamp']}")
        if row["volume"] is None or row["volume"] <= 0.0:
            raise ValueError(f"Upstox nonpositive volume at {row['timestamp']}")
        if row["timestamp"] in result:
            raise ValueError(f"Upstox duplicate candle {row['timestamp']}")
        result[row["timestamp"]] = row

    return [result[key] for key in sorted(result)]


def _expected_timestamps(interval_minutes: int) -> list[str]:
    start = datetime.fromisoformat(f"{TARGET_DAY}T09:15:00+05:30")
    count = EXPECTED_5M_ROWS if interval_minutes == 5 else EXPECTED_1M_ROWS
    return [
        (start + timedelta(minutes=interval_minutes * index)).isoformat()
        for index in range(count)
    ]


def _assert_exact_shape(rows: list[dict[str, Any]], interval_minutes: int) -> None:
    expected = _expected_timestamps(interval_minutes)
    actual = [row["timestamp"] for row in rows]
    if actual != expected:
        raise ValueError(
            f"Upstox {interval_minutes}m session shape mismatch: "
            f"expected {len(expected)} rows, got {len(actual)}"
        )


def _aggregate_target_1m(rows: list[dict[str, Any]]) -> dict[str, float]:
    start = datetime.fromisoformat(TARGET_TIMESTAMP)
    wanted = {
        (start + timedelta(minutes=index)).isoformat()
        for index in range(5)
    }
    chunk = [row for row in rows if row["timestamp"] in wanted]
    chunk.sort(key=lambda row: row["timestamp"])
    if [row["timestamp"] for row in chunk] != sorted(wanted):
        raise ValueError("Upstox target 1-minute chunk is incomplete")
    return {
        "open": float(chunk[0]["open"]),
        "high": max(float(row["high"]) for row in chunk),
        "low": min(float(row["low"]) for row in chunk),
        "close": float(chunk[-1]["close"]),
        "volume": sum(float(row["volume"]) for row in chunk),
    }


def _resolve_contract(
    client: httpx.Client,
    token: str,
) -> dict[str, Any]:
    response = client.get(
        "https://api.upstox.com/v2/expired-instruments/future/contract",
        params={
            "instrument_key": UNDERLYING_KEY,
            "expiry_date": TARGET_EXPIRY,
        },
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    response.raise_for_status()
    payload = response.json()
    candidates = [
        row for row in list(payload.get("data") or [])
        if str(row.get("instrument_type", "")).upper() == "FUT"
        and str(row.get("underlying_symbol", "")).upper() == "NIFTY"
        and str(row.get("expiry", "")) == TARGET_EXPIRY
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"expected exactly one expired NIFTY future for {TARGET_EXPIRY}, "
            f"got {len(candidates)}"
        )
    return candidates[0]


def _fetch_candles(
    client: httpx.Client,
    token: str,
    expired_instrument_key: str,
    interval: str,
) -> list[list[Any]]:
    encoded = quote(expired_instrument_key, safe="")
    url = (
        "https://api.upstox.com/v2/expired-instruments/historical-candle/"
        f"{encoded}/{interval}/{TARGET_DAY}/{TARGET_DAY}"
    )
    response = client.get(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    response.raise_for_status()
    payload = response.json()
    return list(((payload.get("data") or {}).get("candles")) or [])


def verify(
    *,
    input_sha256: str,
    contract: dict[str, Any],
    raw_5m: list[list[Any]],
    raw_1m: list[list[Any]],
) -> dict[str, Any]:
    if input_sha256 != INPUT_SHA256:
        raise ValueError(
            f"input SHA changed: {input_sha256} != {INPUT_SHA256}"
        )
    key = str(contract.get("instrument_key") or "")
    if not key:
        raise ValueError("resolved Upstox expired contract has no instrument_key")

    rows_5m = _normalize_candles(raw_5m, interval_minutes=5)
    rows_1m = _normalize_candles(raw_1m, interval_minutes=1)
    _assert_exact_shape(rows_5m, 5)
    _assert_exact_shape(rows_1m, 1)

    target_5m = [
        row for row in rows_5m
        if row["timestamp"] == TARGET_TIMESTAMP
    ]
    if len(target_5m) != 1:
        raise ValueError("Upstox target five-minute candle missing")
    target_5m = target_5m[0]

    for field, breeze_value in BREEZE_TARGET_OHLC.items():
        if not _close(float(target_5m[field]), float(breeze_value)):
            raise ValueError(
                f"Upstox target {field}={target_5m[field]} does not match "
                f"Breeze {field}={breeze_value}"
            )

    aggregate = _aggregate_target_1m(rows_1m)
    for field in ("open", "high", "low", "close"):
        if not _close(aggregate[field], float(target_5m[field])):
            raise ValueError(
                f"Upstox 1m aggregate {field}={aggregate[field]} does not match "
                f"Upstox 5m {field}={target_5m[field]}"
            )
    if not _close(
        aggregate["volume"],
        float(target_5m["volume"]),
        VOLUME_TOLERANCE,
    ):
        raise ValueError(
            f"Upstox 1m aggregate volume={aggregate['volume']} does not match "
            f"Upstox 5m volume={target_5m['volume']}"
        )

    return {
        "research_type": RESEARCH_TYPE,
        "research_only": True,
        "candidate_frozen": False,
        "blind_data_used": False,
        "implementation_allowed": False,
        "input_sha256": input_sha256,
        "provider": "UPSTOX",
        "contract": contract,
        "target": {
            "day": TARGET_DAY,
            "expiry": TARGET_EXPIRY,
            "timestamp": TARGET_TIMESTAMP,
            "breeze_ohlc": BREEZE_TARGET_OHLC,
            "upstox_5m": target_5m,
            "upstox_1m_aggregate": aggregate,
            "upstox_target_volume_positive": float(target_5m["volume"]) > 0.0,
            "upstox_1m_target_volume_sum_positive": aggregate["volume"] > 0.0,
            "target_ohlc_matches_breeze": True,
            "upstox_1m_matches_upstox_5m": True,
        },
        "session_quality": {
            "five_minute_rows": len(rows_5m),
            "one_minute_rows": len(rows_1m),
            "five_minute_exact_session_shape": True,
            "one_minute_exact_session_shape": True,
        },
        "decision": "INDEPENDENT_PROVIDER_EVIDENCE_PASSED",
        "guardrails": {
            "artifact_modified": False,
            "correction_authorized": False,
            "separate_correction_rule_required": True,
            "blind_data_used": False,
            "strategy_d_remains_paused": True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify isolated Cohort-4 Breeze volume defect with Upstox"
    )
    parser.add_argument("--existing", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    input_sha = _sha256(args.existing)
    if input_sha != INPUT_SHA256:
        raise ValueError(
            f"repaired-input SHA mismatch: {input_sha} != {INPUT_SHA256}"
        )

    _load_local_env()
    token = os.getenv("UPSTOX_ACCESS_TOKEN")
    if not token:
        raise RuntimeError(
            "UPSTOX_ACCESS_TOKEN is required for independent expired-contract verification"
        )

    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        contract = _resolve_contract(client, token)
        key = str(contract["instrument_key"])
        raw_5m = _fetch_candles(client, token, key, "5minute")
        raw_1m = _fetch_candles(client, token, key, "1minute")

    try:
        report = verify(
            input_sha256=input_sha,
            contract=contract,
            raw_5m=raw_5m,
            raw_1m=raw_1m,
        )
    except Exception as exc:
        report = {
            "research_type": "NIFTY_DEVELOPMENT_COHORT_4_UPSTOX_VOLUME_VERIFICATION_FAILED_V1",
            "research_only": True,
            "candidate_frozen": False,
            "blind_data_used": False,
            "implementation_allowed": False,
            "input_sha256": input_sha,
            "provider": "UPSTOX",
            "contract": contract,
            "raw_5m_count": len(raw_5m),
            "raw_1m_count": len(raw_1m),
            "failure": f"{type(exc).__name__}: {exc}",
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
        )
        raise

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(json.dumps({
        "output": str(args.output),
        "decision": report["decision"],
        "instrument_key": report["contract"]["instrument_key"],
        "target_upstox_5m_volume": report["target"]["upstox_5m"]["volume"],
        "target_upstox_1m_volume_sum": report["target"]["upstox_1m_aggregate"]["volume"],
    }, indent=2))


if __name__ == "__main__":
    main()
