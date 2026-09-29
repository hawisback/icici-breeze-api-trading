"""Strict targeted repair for Development Cohort 4 market data.

This module repairs provider-data completeness/validity only. It never fabricates
volume, flips signs, forward-fills, interpolates, or substitutes contracts.

A session is considered valid only when it has exactly the 75 expected regular
five-minute timestamps, valid OHLC, strictly positive actual futures volume and
open interest, and the exact frozen near-month futures contract. Only deficient
sessions are re-fetched, one date at a time, with the frozen expiry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from services.historical.independent_cohort4_protocol import (
    EXPECTED,
    FUTURES_MONTHLY_EXPIRIES,
    SESSION_DATES,
    validate_market,
)
from services.historical.independent_market_research import (
    BreezeFuturesClient,
    _breeze_contract_plan,
    _load_local_env,
    _usable_secret,
)

IST = ZoneInfo("Asia/Kolkata")
RESEARCH_TYPE = "NIFTY_DEVELOPMENT_COHORT_4_MARKET_DATA_REPAIRED_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expected_timestamps(day: str) -> list[str]:
    start = datetime.fromisoformat(f"{day}T09:15:00+05:30")
    return [
        (start + timedelta(minutes=5 * index)).isoformat()
        for index in range(int(EXPECTED["five_minute_bars_per_session"]))
    ]


def _expected_contract_by_date() -> dict[str, str]:
    plan = _breeze_contract_plan(
        [date.fromisoformat(day) for day in SESSION_DATES],
        None,
        list(FUTURES_MONTHLY_EXPIRIES),
    )
    return {
        day.isoformat(): expiry
        for day, expiry in sorted(plan.items())
    }


def _number(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _normalize_refetch(
    rows: list[dict[str, Any]],
    *,
    day: str,
    expiry: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    expected = set(_expected_timestamps(day))
    instrument = f"NIFTY FUT {expiry}"
    normalized: dict[str, dict[str, Any]] = {}
    rejected = Counter()

    for raw in rows:
        try:
            ts = datetime.fromisoformat(str(raw.get("timestamp", "")))
        except ValueError:
            rejected["bad_timestamp"] += 1
            continue
        ts = ts.replace(tzinfo=IST) if ts.tzinfo is None else ts.astimezone(IST)
        ts = ts.replace(second=0, microsecond=0)
        ts_text = ts.isoformat()
        if ts_text not in expected:
            rejected["outside_expected_timestamps"] += 1
            continue

        values = {
            "open": _number(raw.get("open")),
            "high": _number(raw.get("high")),
            "low": _number(raw.get("low")),
            "close": _number(raw.get("close")),
            "volume": _number(raw.get("volume")),
            "open_interest": _number(raw.get("open_interest")),
        }
        if any(value is None for value in values.values()):
            rejected["missing_required_field"] += 1
            continue
        if not (
            values["low"] <= values["open"] <= values["high"]
            and values["low"] <= values["close"] <= values["high"]
        ):
            rejected["invalid_ohlc"] += 1
            continue
        if values["volume"] <= 0.0:
            rejected["nonpositive_volume"] += 1
            continue
        if values["open_interest"] <= 0.0:
            rejected["nonpositive_open_interest"] += 1
            continue
        if str(raw.get("instrument")) != instrument:
            rejected["wrong_contract"] += 1
            continue

        if ts_text in normalized:
            rejected["duplicate_timestamp"] += 1
            continue
        normalized[ts_text] = {
            "timestamp": ts_text,
            "open": values["open"],
            "high": values["high"],
            "low": values["low"],
            "close": values["close"],
            "volume": values["volume"],
            "open_interest": values["open_interest"],
            "source": "BREEZE",
            "instrument": instrument,
            "instrument_type": "Futures",
        }

    ordered = [normalized[key] for key in sorted(normalized)]
    return ordered, dict(rejected)


def _session_issue(
    rows: list[dict[str, Any]],
    *,
    day: str,
    expiry: str,
) -> list[str]:
    issues: list[str] = []
    expected = _expected_timestamps(day)
    day_rows = sorted(
        [row for row in rows if str(row.get("timestamp", ""))[:10] == day],
        key=lambda row: str(row.get("timestamp", "")),
    )
    timestamps = [str(row.get("timestamp", "")) for row in day_rows]
    if len(day_rows) != len(expected):
        issues.append(f"rows={len(day_rows)}")
    if timestamps != expected:
        issues.append("timestamp_set")
    if len(timestamps) != len(set(timestamps)):
        issues.append("duplicates")

    expected_instrument = f"NIFTY FUT {expiry}"
    for row in day_rows:
        values = {
            name: _number(row.get(name))
            for name in ("open", "high", "low", "close", "volume", "open_interest")
        }
        if any(value is None for value in values.values()):
            issues.append("missing_required_field")
            break
        if not (
            values["low"] <= values["open"] <= values["high"]
            and values["low"] <= values["close"] <= values["high"]
        ):
            issues.append("invalid_ohlc")
            break
        if values["volume"] <= 0.0:
            issues.append("nonpositive_volume")
            break
        if values["open_interest"] <= 0.0:
            issues.append("nonpositive_open_interest")
            break
        if str(row.get("instrument")) != expected_instrument:
            issues.append("wrong_contract")
            break
    return sorted(set(issues))


def _quality(
    rows: list[dict[str, Any]],
    contract_by_date: dict[str, str],
) -> dict[str, Any]:
    counts = Counter(str(row["timestamp"])[:10] for row in rows)
    timestamps = [str(row["timestamp"]) for row in rows]
    invalid_ohlc = 0
    missing_volume = 0
    missing_oi = 0
    nonpositive_volume = 0
    nonpositive_oi = 0
    wrong_contract = 0

    for row in rows:
        day = str(row["timestamp"])[:10]
        o, h, l, c = (
            _number(row.get("open")),
            _number(row.get("high")),
            _number(row.get("low")),
            _number(row.get("close")),
        )
        volume = _number(row.get("volume"))
        oi = _number(row.get("open_interest"))
        if None in (o, h, l, c):
            invalid_ohlc += 1
        elif not (l <= o <= h and l <= c <= h):
            invalid_ohlc += 1
        if volume is None:
            missing_volume += 1
        elif volume <= 0.0:
            nonpositive_volume += 1
        if oi is None:
            missing_oi += 1
        elif oi <= 0.0:
            nonpositive_oi += 1
        expected = f"NIFTY FUT {contract_by_date.get(day)}"
        wrong_contract += str(row.get("instrument")) != expected

    return {
        "sessions": len(SESSION_DATES),
        "rows": len(rows),
        "duplicate_rows": len(timestamps) - len(set(timestamps)),
        "invalid_ohlc_rows": int(invalid_ohlc),
        "missing_volume_rows": int(missing_volume),
        "missing_open_interest_rows": int(missing_oi),
        "nonpositive_volume_rows": int(nonpositive_volume),
        "nonpositive_open_interest_rows": int(nonpositive_oi),
        "wrong_contract_rows": int(wrong_contract),
        "complete_75_bar_sessions": sum(
            counts.get(day, 0) == int(EXPECTED["five_minute_bars_per_session"])
            for day in SESSION_DATES
        ),
        "rows_by_session": {
            day: int(counts.get(day, 0)) for day in SESSION_DATES
        },
        "expected_rows_by_session": {
            day: int(EXPECTED["five_minute_bars_per_session"])
            for day in SESSION_DATES
        },
    }


def _canonical_from_futures(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "timestamp": row["timestamp"],
            "spot_open": None,
            "spot_high": None,
            "spot_low": None,
            "spot_close": None,
            "spot_source": None,
            "futures_open": row["open"],
            "futures_high": row["high"],
            "futures_low": row["low"],
            "futures_close": row["close"],
            "futures_volume": row["volume"],
            "futures_open_interest": row["open_interest"],
            "futures_instrument": row["instrument"],
            "futures_basis_points": None,
            "futures_source": "BREEZE",
            "vix_open": None,
            "vix_high": None,
            "vix_low": None,
            "vix_close": None,
            "vix_source": None,
            "niftybees_volume": None,
        }
        for row in rows
    ]


def repair_market(
    payload: dict[str, Any],
    *,
    client: BreezeFuturesClient,
    attempts_per_session: int = 3,
    input_sha256: str | None = None,
) -> dict[str, Any]:
    if attempts_per_session < 1:
        raise ValueError("attempts_per_session must be >= 1")
    if list(payload.get("session_dates") or []) != SESSION_DATES:
        raise ValueError("existing market sessions differ from frozen Cohort 4 protocol")

    contract_by_date = _expected_contract_by_date()
    existing_contracts = (
        (payload.get("provenance") or {})
        .get("nifty_futures", {})
        .get("breeze_contract_by_date")
        or {}
    )
    if dict(existing_contracts) != contract_by_date:
        raise ValueError("existing market contract map differs from frozen protocol")

    existing_rows = list(payload.get("nifty_futures") or [])
    deficient = {
        day: _session_issue(existing_rows, day=day, expiry=contract_by_date[day])
        for day in SESSION_DATES
    }
    deficient = {day: issues for day, issues in deficient.items() if issues}

    repaired_rows = list(existing_rows)
    repaired_sessions: list[str] = []
    retries: dict[str, list[dict[str, Any]]] = {}

    for day in sorted(deficient):
        expiry = contract_by_date[day]
        attempts: list[dict[str, Any]] = []
        replacement: list[dict[str, Any]] | None = None
        for attempt in range(1, attempts_per_session + 1):
            error: str | None = None
            fetched: list[dict[str, Any]] = []
            try:
                fetched = client.history([date.fromisoformat(day)], expiry)
            except Exception as exc:  # provider diagnostics retained; no fabrication
                error = str(exc)
            normalized, rejected = _normalize_refetch(
                fetched, day=day, expiry=expiry
            )
            issues = _session_issue(
                normalized, day=day, expiry=expiry
            )
            attempts.append({
                "attempt": attempt,
                "expiry": expiry,
                "provider_error": error,
                "raw_normalized_client_rows": len(fetched),
                "accepted_rows": len(normalized),
                "reject_counts": rejected,
                "remaining_issues": issues,
            })
            if not issues:
                replacement = normalized
                break
        retries[day] = attempts
        if replacement is not None:
            repaired_rows = [
                row for row in repaired_rows
                if str(row.get("timestamp", ""))[:10] != day
            ]
            repaired_rows.extend(replacement)
            repaired_sessions.append(day)

    repaired_rows = sorted(
        repaired_rows, key=lambda row: str(row["timestamp"])
    )
    remaining = {
        day: _session_issue(repaired_rows, day=day, expiry=contract_by_date[day])
        for day in SESSION_DATES
    }
    remaining = {day: issues for day, issues in remaining.items() if issues}
    quality = _quality(repaired_rows, contract_by_date)

    report = dict(payload)
    report["research_type"] = RESEARCH_TYPE
    report["research_only"] = True
    report["candidate_frozen"] = False
    report["blind_data_used"] = False
    report["implementation_allowed"] = False
    report["nifty_futures"] = repaired_rows
    report["canonical_market_rows"] = _canonical_from_futures(repaired_rows)
    report.setdefault("provider_series", {}).setdefault("BREEZE", {})[
        "NIFTY_FUTURES"
    ] = repaired_rows
    report["quality"] = quality
    report.setdefault("provider_quality", {}).setdefault(
        "nifty_futures", {}
    ).setdefault("providers", {})["BREEZE"] = quality
    report["coverage"]["nifty_futures_rows"] = len(repaired_rows)
    report["coverage"]["futures_dates"] = list(SESSION_DATES)
    report["repair"] = {
        "input_sha256": input_sha256,
        "initial_deficient_sessions": deficient,
        "repaired_sessions": repaired_sessions,
        "remaining_deficient_sessions": remaining,
        "attempts_per_session": attempts_per_session,
        "retry_diagnostics": retries,
        "policy": {
            "only_deficient_sessions_refetched": True,
            "one_session_per_request": True,
            "frozen_contracts_only": True,
            "synthetic_fill": False,
            "interpolation": False,
            "forward_fill": False,
            "sign_correction": False,
        },
    }

    if not remaining:
        validate_market(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Strictly repair deficient Development Cohort 4 market sessions"
    )
    parser.add_argument("--existing", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--attempts-per-session", type=int, default=3)
    args = parser.parse_args()

    input_sha = _sha256(args.existing)
    payload = json.loads(args.existing.read_text(encoding="utf-8"))

    _load_local_env()
    key = _usable_secret("BREEZE_API_KEY")
    secret = _usable_secret("BREEZE_SECRET_KEY")
    token = _usable_secret("BREEZE_SESSION_TOKEN")
    if not (key and secret and token):
        raise RuntimeError(
            "BREEZE_API_KEY, BREEZE_SECRET_KEY and BREEZE_SESSION_TOKEN are required"
        )

    report = repair_market(
        payload,
        client=BreezeFuturesClient(key, secret, token),
        attempts_per_session=args.attempts_per_session,
        input_sha256=input_sha,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )

    remaining = report["repair"]["remaining_deficient_sessions"]
    print(json.dumps({
        "output": str(args.output),
        "input_sha256": input_sha,
        "initial_deficient_sessions": report["repair"]["initial_deficient_sessions"],
        "repaired_sessions": report["repair"]["repaired_sessions"],
        "remaining_deficient_sessions": remaining,
        "quality": report["quality"],
    }, indent=2))
    if remaining:
        raise ValueError(
            "Cohort-4 market repair remains incomplete; output retained for diagnostics"
        )


if __name__ == "__main__":
    main()
