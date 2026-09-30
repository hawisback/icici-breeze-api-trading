"""Collect sealed Breeze session artifacts for the frozen prospective replication.

The frozen protocol permits a completed session to be collected and shape-QA'd
after the regular 15:40 IST close. It forbids predictor/target scoring or
outcome inspection until the full 30-session sample is complete.

This module therefore supports:
1. one-session QA-only collection after that session's close; and
2. assembly of the 30 sealed session artifacts into the exact full market
   artifact after every frozen session is present.

The findings analyzer remains separately locked until 2026-11-16 15:40 IST.
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
SESSION_RESEARCH_TYPE = "NIFTY_PROSPECTIVE_MAGNITUDE_SESSION_QA_V1"


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


def _session_completion(day: str) -> datetime:
    return datetime.fromisoformat(
        f"{day}T{SESSION_END_EXCLUSIVE}:00+05:30"
    ).astimezone(IST)


def assert_session_collection_open(
    day: str,
    now: datetime | None = None,
) -> None:
    if day not in SESSION_DATES:
        raise ValueError(f"{day} is not a frozen prospective session")
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    else:
        current = current.astimezone(IST)
    if current < _session_completion(day):
        raise RuntimeError(
            f"Prospective session {day} collection is locked until "
            f"{_session_completion(day).isoformat()}."
        )


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


def _normalize_session_response(
    *,
    day: str,
    expiry: str,
    response: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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
    if actual != expected:
        raise ValueError(
            f"{day} exact prospective shape mismatch: "
            f"expected {len(expected)} bars, got {len(actual)}"
        )

    diagnostics = {
        "date": day,
        "futures_expiry": expiry,
        "status": (response or {}).get("Status"),
        "error": (response or {}).get("Error"),
        "raw_count": len(success),
        "accepted_count": len(ordered),
    }
    return ordered, diagnostics


def collect_session(
    day: str,
    *,
    now: datetime | None = None,
    breeze: Any | None = None,
) -> dict[str, Any]:
    """Collect exactly one completed frozen session and perform QA only."""
    assert_session_collection_open(day, now)
    expiry = CONTRACT_BY_DATE[day]
    client = breeze or _breeze_client()
    response = client.get_historical_data_v2(
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
    rows, diagnostics = _normalize_session_response(
        day=day,
        expiry=expiry,
        response=response,
    )
    return {
        "research_type": SESSION_RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "research_only": True,
        "prospective_sample": True,
        "qa_only": True,
        "outcome_scoring_performed": False,
        "candidate_frozen": False,
        "blind_validation_opened": False,
        "implementation_allowed": False,
        "provider": "BREEZE",
        "session_date": day,
        "futures_expiry": expiry,
        "session_start": SESSION_START,
        "session_end_exclusive": SESSION_END_EXCLUSIVE,
        "bars_per_session": EXPECTED_BARS_PER_SESSION,
        "rows": len(rows),
        "quality": {
            "rows": len(rows),
            "complete_77_bar_session": True,
            "duplicate_rows": 0,
            "invalid_ohlc_rows": 0,
            "missing_volume_rows": 0,
            "missing_open_interest_rows": 0,
            "nonpositive_volume_rows": 0,
            "nonpositive_open_interest_rows": 0,
            "wrong_contract_rows": 0,
        },
        "request_diagnostics": diagnostics,
        "futures_rows": rows,
    }


def _validate_session_artifact(
    payload: dict[str, Any],
    expected_day: str,
) -> list[dict[str, Any]]:
    if payload.get("research_type") != SESSION_RESEARCH_TYPE:
        raise ValueError(f"{expected_day} wrong session research type")
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError(f"{expected_day} protocol version changed")
    if payload.get("provider") != "BREEZE":
        raise ValueError(f"{expected_day} provider must remain BREEZE")
    if payload.get("qa_only") is not True:
        raise ValueError(f"{expected_day} session artifact must remain QA-only")
    if payload.get("outcome_scoring_performed") is not False:
        raise ValueError(f"{expected_day} outcome scoring must not be performed")
    if payload.get("session_date") != expected_day:
        raise ValueError(f"{expected_day} session date mismatch")
    expiry = CONTRACT_BY_DATE[expected_day]
    if payload.get("futures_expiry") != expiry:
        raise ValueError(f"{expected_day} wrong futures expiry")
    if int(payload.get("bars_per_session", -1)) != EXPECTED_BARS_PER_SESSION:
        raise ValueError(f"{expected_day} bars-per-session changed")
    if int(payload.get("rows", -1)) != EXPECTED_BARS_PER_SESSION:
        raise ValueError(f"{expected_day} row count changed")

    rows = list(payload.get("futures_rows") or [])
    if len(rows) != EXPECTED_BARS_PER_SESSION:
        raise ValueError(f"{expected_day} futures_rows count changed")
    actual = [str(row.get("timestamp")) for row in rows]
    if actual != _expected_timestamps(expected_day):
        raise ValueError(f"{expected_day} exact timestamp shape changed")
    for row in rows:
        if row.get("source") != "BREEZE":
            raise ValueError(f"{expected_day} contains non-Breeze row")
        if row.get("futures_expiry") != expiry:
            raise ValueError(f"{expected_day} contains wrong futures expiry")
        if row.get("instrument") != f"NIFTY FUT {expiry}":
            raise ValueError(f"{expected_day} contains wrong instrument label")
    return rows


def assemble_session_artifacts(session_dir: Path) -> dict[str, Any]:
    """Assemble all 30 sealed session files without computing any findings."""
    all_rows: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for day in SESSION_DATES:
        path = session_dir / f"{day}.json"
        if not path.exists():
            raise ValueError(f"missing sealed session artifact {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = _validate_session_artifact(payload, day)
        all_rows.extend(rows)
        diagnostics.append(dict(payload.get("request_diagnostics") or {}))

    if len(all_rows) != EXPECTED_ROWS:
        raise ValueError(f"expected {EXPECTED_ROWS} rows, got {len(all_rows)}")

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "research_only": True,
        "prospective_sample": True,
        "qa_only_assembly": True,
        "outcome_scoring_performed": False,
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


def _write_report(report: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "output": str(output),
                "sha256": digest,
                "rows": report["rows"],
                "qa_only": bool(
                    report.get("qa_only") or report.get("qa_only_assembly")
                ),
                "outcome_scoring_performed": report.get(
                    "outcome_scoring_performed", False
                ),
            },
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect/assemble frozen prospective NIFTY magnitude data"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--session", choices=SESSION_DATES)
    group.add_argument("--assemble-dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    _load_local_env()

    if args.session:
        report = collect_session(args.session)
        output = args.output or Path(
            f"data/independent_prospective_magnitude_sessions/{args.session}.json"
        )
    else:
        report = assemble_session_artifacts(args.assemble_dir)
        output = args.output or Path(
            "data/independent_prospective_magnitude_market.json"
        )
    _write_report(report, output)


if __name__ == "__main__":
    main()
