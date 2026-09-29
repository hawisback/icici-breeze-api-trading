"""Protocol-driven exact-date market collection for Development Cohort 4.

Unlike the generic rolling-session collector, this module never discovers dates
from a lookback window. It requests exactly the 80 dates frozen in the Cohort-4
protocol and exactly the predeclared near-month futures contracts. This avoids
calendar/expiry drift and keeps the new cohort development-only.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from services.historical.independent_cohort4_protocol import (
    EXPECTED,
    FUTURES_MONTHLY_EXPIRIES,
    SESSION_DATES,
    validate_market,
)
from services.historical.independent_market_research import (
    BreezeFuturesClient,
    _breeze_contract_plan,
    _canonical_rows,
    _fetch_breeze_contract_plan,
    _load_local_env,
    _usable_secret,
)


def _quality(rows: list[Any], plan: dict[date, str]) -> dict[str, Any]:
    counts = Counter(
        datetime.fromisoformat(str(row.timestamp)).date().isoformat()
        for row in rows
    )
    timestamps = [str(row.timestamp) for row in rows]
    duplicate_rows = len(timestamps) - len(set(timestamps))
    invalid_ohlc = sum(
        not (
            float(row.low) <= float(row.open) <= float(row.high)
            and float(row.low) <= float(row.close) <= float(row.high)
        )
        for row in rows
    )
    missing_volume = sum(row.volume is None for row in rows)
    missing_oi = sum(row.open_interest is None for row in rows)
    wrong_contract = 0
    for row in rows:
        day = datetime.fromisoformat(str(row.timestamp)).date()
        expected = f"NIFTY FUT {plan[day]}"
        wrong_contract += str(row.instrument) != expected
    expected_counts = {
        day: int(EXPECTED["five_minute_bars_per_session"])
        for day in SESSION_DATES
    }
    return {
        "sessions": len(SESSION_DATES),
        "rows": len(rows),
        "duplicate_rows": duplicate_rows,
        "invalid_ohlc_rows": int(invalid_ohlc),
        "missing_volume_rows": int(missing_volume),
        "missing_open_interest_rows": int(missing_oi),
        "wrong_contract_rows": int(wrong_contract),
        "complete_75_bar_sessions": sum(
            counts.get(day, 0) == int(EXPECTED["five_minute_bars_per_session"])
            for day in SESSION_DATES
        ),
        "rows_by_session": {
            day: int(counts.get(day, 0)) for day in SESSION_DATES
        },
        "expected_rows_by_session": expected_counts,
    }


def collect(client: BreezeFuturesClient) -> dict[str, Any]:
    dates = [date.fromisoformat(value) for value in SESSION_DATES]
    plan = _breeze_contract_plan(
        dates,
        None,
        list(FUTURES_MONTHLY_EXPIRIES),
    )
    rows, diagnostics = _fetch_breeze_contract_plan(client, plan)
    quality = _quality(rows, plan)

    failures = []
    if quality["rows"] != int(EXPECTED["five_minute_rows"]):
        failures.append(
            f"rows={quality['rows']} expected={EXPECTED['five_minute_rows']}"
        )
    for key in (
        "duplicate_rows",
        "invalid_ohlc_rows",
        "missing_volume_rows",
        "missing_open_interest_rows",
        "wrong_contract_rows",
    ):
        if quality[key] != 0:
            failures.append(f"{key}={quality[key]}")
    if quality["complete_75_bar_sessions"] != int(EXPECTED["sessions"]):
        failures.append(
            "complete_75_bar_sessions="
            f"{quality['complete_75_bar_sessions']} expected={EXPECTED['sessions']}"
        )
    if failures:
        raise ValueError(
            "Cohort-4 exact-date Breeze futures QA failed: " + "; ".join(failures)
        )

    contract_by_date = {
        day.isoformat(): expiry for day, expiry in sorted(plan.items())
    }
    canonical = _canonical_rows([], rows, [], [], "BREEZE", None)
    report = {
        "research_type": "NIFTY_DEVELOPMENT_COHORT_4_MARKET_DATA_V1",
        "research_only": True,
        "candidate_frozen": False,
        "blind_data_used": False,
        "implementation_allowed": False,
        "interval_minutes": 5,
        "session_dates": list(SESSION_DATES),
        "selection_policy": {
            "dates": "exact frozen Cohort-4 session list; no rolling discovery",
            "futures_contracts": "explicit frozen near-month schedule",
            "contract_stitching": False,
        },
        "provenance": {
            "nifty_index": {
                "available": False,
                "source": None,
                "volume_semantics": "not_used",
                "note": "standalone spot collector is authoritative for Cohort 4",
            },
            "nifty_futures": {
                "canonical_source": "BREEZE",
                "available": True,
                "contract_selection": "explicit frozen near-month expiry schedule",
                "breeze_contract_by_date": contract_by_date,
                "public_contracts_by_date": {},
                "volume_semantics": "actual futures traded volume",
                "open_interest_semantics": "provider-reported futures open interest",
            },
            "india_vix": {
                "canonical_source": None,
                "available": False,
                "note": "standalone VIX collector is authoritative for Cohort 4",
            },
            "nifty_volume_proxy": {
                "available": False,
                "note": "not used; actual futures volume is collected",
            },
        },
        "coverage": {
            "nifty_index_rows": 0,
            "nifty_futures_rows": len(rows),
            "india_vix_rows": 0,
            "nifty_volume_proxy_rows": 0,
            "futures_dates": list(SESSION_DATES),
            "vix_dates": [],
            "volume_proxy_dates": [],
        },
        "credentialed_sources": {
            "BREEZE": {
                "available": True,
                "series": ["NIFTY_FUTURES"],
                "instrument_config": {
                    "mode": "near_month_schedule",
                    "near_month_expiries": list(FUTURES_MONTHLY_EXPIRIES),
                    "contract_by_date": contract_by_date,
                },
                "diagnostics_by_expiry": diagnostics,
            }
        },
        "provider_quality": {
            "nifty_futures": {
                "canonical_source": "BREEZE",
                "providers": {"BREEZE": quality},
            }
        },
        "canonical_market_rows": canonical,
        "provider_series": {
            "BREEZE": {
                "NIFTY_FUTURES": [asdict(row) for row in rows],
            }
        },
        "nifty_index": [],
        "nifty_futures": [asdict(row) for row in rows],
        "india_vix": [],
        "nifty_volume_proxy": [],
        "quality": quality,
    }
    validate_market(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collect exact frozen Development Cohort 4 NIFTY futures"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    _load_local_env()
    key = _usable_secret("BREEZE_API_KEY")
    secret = _usable_secret("BREEZE_SECRET_KEY")
    token = _usable_secret("BREEZE_SESSION_TOKEN")
    if not (key and secret and token):
        raise RuntimeError(
            "BREEZE_API_KEY, BREEZE_SECRET_KEY and BREEZE_SESSION_TOKEN are required"
        )

    try:
        report = collect(BreezeFuturesClient(key, secret, token))
    except Exception as exc:
        message = str(exc)
        if "session key is expired" in message.lower():
            raise RuntimeError(
                "Breeze rejected the configured BREEZE_SESSION_TOKEN as expired. "
                "Generate a fresh Breeze session token, update BREEZE_SESSION_TOKEN "
                "in the local environment/.env, then rerun. No Cohort-4 data were "
                "accepted."
            ) from exc
        raise

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(json.dumps({
        "output": str(args.output),
        "validation": "PASSED",
        "session_dates": report["session_dates"],
        "quality": report["quality"],
    }, indent=2))


if __name__ == "__main__":
    main()
