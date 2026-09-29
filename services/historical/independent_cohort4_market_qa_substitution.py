"""Apply the frozen Cohort-4 QA session substitution using Breeze only."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from services.historical.external_trading_api_consent_guardrail import POLICY
from services.historical.independent_cohort4_market_repair import (
    _canonical_from_futures,
    _load_local_env,
    _normalize_refetch,
    _usable_secret,
)
from services.historical.independent_cohort4_qa_amendment import (
    EXPECTED,
    FAILED_REPAIR_SHA256,
    REMOVED_SESSION,
    REPLACEMENT_FUTURES_EXPIRY,
    REPLACEMENT_SESSION,
    SESSION_DATES,
    expected_contract_by_date,
    validate_amended_market,
)
from services.historical.research_provider_clients import BreezeFuturesClient

RESEARCH_TYPE = "NIFTY_DEVELOPMENT_COHORT_4_MARKET_DATA_QA_SUBSTITUTED_V1"


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


def _quality(rows: list[dict[str, Any]], contract_by_date: dict[str, str]) -> dict[str, Any]:
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
        if None in (o, h, l, c) or not (l <= o <= h and l <= c <= h):
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
        if str(row.get("instrument")) != expected:
            wrong_contract += 1

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
        "rows_by_session": {day: int(counts.get(day, 0)) for day in SESSION_DATES},
        "expected_rows_by_session": {
            day: int(EXPECTED["five_minute_bars_per_session"])
            for day in SESSION_DATES
        },
    }


def apply_substitution(
    payload: dict[str, Any],
    *,
    client: BreezeFuturesClient,
    input_sha256: str,
) -> dict[str, Any]:
    if input_sha256 != FAILED_REPAIR_SHA256:
        raise ValueError(
            f"failed-repair input SHA changed: {input_sha256} != {FAILED_REPAIR_SHA256}"
        )
    if POLICY["current_cohort4_qa_authorized_providers"] != ["BREEZE"]:
        raise ValueError("Cohort-4 QA provider policy changed")
    if payload.get("blind_data_used") is not False:
        raise ValueError("Cohort-4 substitution must remain non-blind")
    if payload.get("implementation_allowed") is not False:
        raise ValueError("Cohort-4 substitution must remain research-only")

    original_rows = list(payload.get("nifty_futures") or [])
    removed = [
        row for row in original_rows
        if str(row.get("timestamp", ""))[:10] == REMOVED_SESSION
    ]
    if len(removed) != int(EXPECTED["five_minute_bars_per_session"]):
        raise ValueError(
            f"expected 75 rows for removed session, got {len(removed)}"
        )
    retained = [
        row for row in original_rows
        if str(row.get("timestamp", ""))[:10] != REMOVED_SESSION
    ]

    fetched = client.history(
        [date.fromisoformat(REPLACEMENT_SESSION)],
        REPLACEMENT_FUTURES_EXPIRY,
    )
    replacement, rejected = _normalize_refetch(
        fetched,
        day=REPLACEMENT_SESSION,
        expiry=REPLACEMENT_FUTURES_EXPIRY,
    )
    if rejected:
        raise ValueError(
            f"replacement session contains rejected rows: {rejected}"
        )
    if len(replacement) != int(EXPECTED["five_minute_bars_per_session"]):
        raise ValueError(
            f"replacement session expected 75 rows, got {len(replacement)}"
        )

    rows = sorted(
        retained + replacement,
        key=lambda row: str(row["timestamp"]),
    )
    if len(rows) != int(EXPECTED["five_minute_rows"]):
        raise ValueError(f"amended market expected 6000 rows, got {len(rows)}")

    contract_by_date = expected_contract_by_date()
    quality = _quality(rows, contract_by_date)

    report = json.loads(json.dumps(payload))
    report["research_type"] = RESEARCH_TYPE
    report["research_only"] = True
    report["candidate_frozen"] = False
    report["blind_data_used"] = False
    report["implementation_allowed"] = False
    report["session_dates"] = list(SESSION_DATES)
    report["nifty_futures"] = rows
    report["canonical_market_rows"] = _canonical_from_futures(rows)
    report.setdefault("provider_series", {}).setdefault("BREEZE", {})[
        "NIFTY_FUTURES"
    ] = rows
    report["quality"] = quality

    provenance = report.setdefault("provenance", {}).setdefault("nifty_futures", {})
    provenance["canonical_source"] = "BREEZE"
    provenance["breeze_contract_by_date"] = contract_by_date
    provenance["contract_selection"] = "QA-amended frozen near-month expiry schedule"
    provenance["volume_semantics"] = "actual futures traded volume"
    provenance["open_interest_semantics"] = "provider-reported futures open interest"

    coverage = report.setdefault("coverage", {})
    coverage["nifty_futures_rows"] = len(rows)
    coverage["futures_dates"] = list(SESSION_DATES)

    instrument_config = (
        report.setdefault("credentialed_sources", {})
        .setdefault("BREEZE", {})
        .setdefault("instrument_config", {})
    )
    instrument_config["contract_by_date"] = contract_by_date

    provider_quality = (
        report.setdefault("provider_quality", {})
        .setdefault("nifty_futures", {})
        .setdefault("providers", {})
    )
    provider_quality["BREEZE"] = quality

    report["qa_amendment"] = {
        "version": "DEVELOPMENT_COHORT_4_QA_SUBSTITUTION_V1",
        "input_sha256": input_sha256,
        "removed_session": REMOVED_SESSION,
        "replacement_session": REPLACEMENT_SESSION,
        "replacement_expiry": REPLACEMENT_FUTURES_EXPIRY,
        "replacement_provider": "BREEZE",
        "replacement_request_diagnostics": dict(
            getattr(client, "last_history_debug", {}) or {}
        ),
        "reason": "irreconcilable_provider_volume_invalidity",
        "strategy_outcomes_used": False,
        "new_external_broker_api_used": False,
        "synthetic_fill": False,
        "abs_volume_transform": False,
        "interpolation": False,
        "forward_fill": False,
    }

    validate_amended_market(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Apply QA-only Cohort-4 session substitution with Breeze"
    )
    parser.add_argument("--existing", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
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

    report = apply_substitution(
        payload,
        client=BreezeFuturesClient(key, secret, token),
        input_sha256=input_sha,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(json.dumps({
        "output": str(args.output),
        "validation": "PASSED",
        "removed_session": REMOVED_SESSION,
        "replacement_session": REPLACEMENT_SESSION,
        "quality": report["quality"],
    }, indent=2))


if __name__ == "__main__":
    main()
