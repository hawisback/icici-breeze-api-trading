"""Frozen protocol for independent NIFTY Development Cohort 2.

This protocol is committed before Cohort-2 market data are collected or scored.
It is a replication cohort, not a discovery or blind-validation cohort.

External schedule basis:
- NSE/FAOP/68685 (23-Jun-2025): NIFTY weekly expiry moved to Tuesday and
  explicitly realigned Sep/Oct/Nov/Dec 2025 monthly contracts.
- NSE/FAOP/65588 (13-Dec-2024): 2025 F&O holiday calendar.
- NSE/FAOP/70320 (22-Sep-2025): 21-Oct-2025 was a one-hour Muhurat session,
  so it is excluded from the full 75-bar regular-session cohort.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "DEVELOPMENT_COHORT_2_V1_1"
COHORT_ROLE = "OUT_OF_SAMPLE_REPLICATION_DEVELOPMENT_NOT_BLIND_VALIDATION"

SESSION_DATES = [
    "2025-09-09", "2025-09-10", "2025-09-11", "2025-09-12",
    "2025-09-15", "2025-09-16", "2025-09-17", "2025-09-18",
    "2025-09-19", "2025-09-22", "2025-09-23", "2025-09-24",
    "2025-09-25", "2025-09-26", "2025-09-29", "2025-09-30",
    "2025-10-01", "2025-10-03", "2025-10-06", "2025-10-07",
    "2025-10-08", "2025-10-09", "2025-10-10", "2025-10-13",
    "2025-10-14", "2025-10-15", "2025-10-16", "2025-10-17",
    "2025-10-20", "2025-10-23", "2025-10-24", "2025-10-27",
    "2025-10-28", "2025-10-29", "2025-10-30", "2025-10-31",
    "2025-11-03", "2025-11-04", "2025-11-06", "2025-11-07",
    "2025-11-10", "2025-11-11", "2025-11-12", "2025-11-13",
    "2025-11-14", "2025-11-17", "2025-11-18", "2025-11-19",
    "2025-11-20", "2025-11-21", "2025-11-24", "2025-11-25",
    "2025-11-26", "2025-11-27", "2025-11-28", "2025-12-01",
    "2025-12-02", "2025-12-03", "2025-12-04", "2025-12-05",
    "2025-12-08", "2025-12-09", "2025-12-10", "2025-12-11",
    "2025-12-12", "2025-12-15", "2025-12-16", "2025-12-17",
    "2025-12-18", "2025-12-19", "2025-12-22", "2025-12-23",
]
BLOCKS = [SESSION_DATES[index:index + 12] for index in range(0, 72, 12)]

FUTURES_EXPIRIES = [
    "2025-09-30",
    "2025-10-28",
    "2025-11-25",
    "2025-12-30",
]
OPTION_EXPIRIES = [
    "2025-09-09", "2025-09-16", "2025-09-23", "2025-09-30",
    "2025-10-07", "2025-10-14", "2025-10-20", "2025-10-28",
    "2025-11-04", "2025-11-11", "2025-11-18", "2025-11-25",
    "2025-12-02", "2025-12-09", "2025-12-16", "2025-12-23",
]

EXCLUDED_SPECIAL_OR_HOLIDAY_DATES = {
    "2025-10-02": "NSE F&O holiday",
    "2025-10-21": "one-hour Diwali Muhurat session; not a regular 75-bar session",
    "2025-10-22": "NSE F&O holiday",
    "2025-11-05": "NSE F&O holiday",
}

PREVIOUSLY_INSPECTED_WINDOWS = [
    ("2025-12-24", "2026-01-07", "Blind11"),
    ("2026-01-08", "2026-01-22", "Blind10"),
    ("2026-01-23", "2026-02-06", "Blind09"),
    ("2026-02-09", "2026-02-20", "Blind08"),
    ("2026-04-01", "2026-04-16", "Blind07"),
    ("2026-04-17", "2026-04-30", "Blind06"),
    ("2026-05-04", "2026-05-15", "Blind05"),
    ("2026-05-19", "2026-09-09", "Development Cohort 1"),
]

EXPECTED = {
    "sessions": 72,
    "blocks": 6,
    "sessions_per_block": 12,
    "five_minute_rows": 5400,
    "vix_rows": 5400,
    "spot_rows": 5400,
    "dynamic_option_contract_rows": 97200,
    "one_minute_rows_if_intrabar_stage_runs": 27000,
    "five_minute_bars_per_session": 75,
    "one_minute_bars_per_session": 375,
}

MEASUREMENTS_FROZEN_BEFORE_COLLECTION = {
    "futures_sequence": {
        "windows_bars": [3, 6],
        "future_horizons_minutes": [10, 15, 30, 60],
        "entry_reference": "next 5-minute bar open",
        "descriptors": [
            "net return",
            "path length",
            "path efficiency",
            "range",
            "close location in sequence range",
            "volume expansion versus preceding equal window",
            "OI change",
            "price x OI interaction",
            "absolute-return acceleration",
        ],
        "primary_replication_checks": [
            "3-bar range vs next-30m max excursion",
            "6-bar range vs next-30m max excursion",
            "3-bar close-location vs next-10m terminal return",
        ],
    },
    "vix_context": {
        "primary_check": (
            "incremental INDIA VIX movement-state information after recent "
            "futures range and current futures state"
        ),
        "features": ["VIX level", "VIX 5-minute change", "VIX 15-minute change"],
    },
    "options_context": {
        "primary_checks": [
            "ATM straddle movement-state information after futures range plus VIX",
            "ATM synthetic-forward relative-move lead vs next 5-minute futures",
            "spot-attributed cross-fitted options-specific lead vs next 5-minute futures",
        ],
        "strike_step": 50,
        "strike_radius_each_side": 4,
        "contract_stitching": False,
    },
    "intrabar": {
        "conditional_stage": (
            "run the already-defined 1-minute timing replication only after "
            "the 5-minute options-specific lead result is recorded"
        ),
        "no_threshold_selection": True,
    },
}

REPLICATION_RULES = {
    "purpose": (
        "Measure whether already-observed relationships reproduce out of sample; "
        "do not discover or tune new features in Cohort 2."
    ),
    "block_rule": "6 chronological blocks x 12 sessions fixed before collection",
    "directional_replication_label": (
        "same pooled sign as Cohort 1 and same sign in at least 5 of 6 Cohort-2 blocks"
    ),
    "magnitude": (
        "report Cohort-2 effect size and Cohort2/Cohort1 ratio; magnitude is not "
        "optimized and no post-hoc cutoff is used"
    ),
    "failure_rule": (
        "If a frozen relationship misses the sign/block criterion, record non-replication. "
        "Do not rescue it with DTE, time, magnitude, strike, or horizon filters."
    ),
    "candidate_freeze": False,
    "blind_validation": False,
    "pnl_optimization": False,
    "threshold_optimization": False,
}

PROTOCOL_CORRECTION = {
    "from_version": "DEVELOPMENT_COHORT_2_V1",
    "to_version": PROTOCOL_VERSION,
    "field": "OPTION_EXPIRIES",
    "incorrect_value": "2025-10-21",
    "correct_value": "2025-10-20",
    "reason": (
        "21-Oct-2025 was an NSE F&O holiday with a special Muhurat session; "
        "the NIFTY weekly expiry for that week was Monday 20-Oct-2025."
    ),
    "trigger": "strict ATM CE/PE coverage QA failed during first options collection",
    "data_use_before_correction": (
        "No Cohort-2 options price/outcome analysis or replication scoring was performed."
    ),
}

PROTOCOL = {
    "protocol_version": PROTOCOL_VERSION,
    "cohort_role": COHORT_ROLE,
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "start_date": SESSION_DATES[0],
    "end_date": SESSION_DATES[-1],
    "session_dates": SESSION_DATES,
    "blocks": BLOCKS,
    "futures_expiries": FUTURES_EXPIRIES,
    "option_expiries": OPTION_EXPIRIES,
    "protocol_correction": PROTOCOL_CORRECTION,
    "excluded_dates": EXCLUDED_SPECIAL_OR_HOLIDAY_DATES,
    "previously_inspected_windows": PREVIOUSLY_INSPECTED_WINDOWS,
    "expected": EXPECTED,
    "measurements": MEASUREMENTS_FROZEN_BEFORE_COLLECTION,
    "replication_rules": REPLICATION_RULES,
}


def futures_expiry_for_day(day: str) -> str:
    parsed = date.fromisoformat(day)
    for expiry in FUTURES_EXPIRIES:
        if parsed <= date.fromisoformat(expiry):
            return expiry
    raise ValueError(f"no frozen futures expiry covers {day}")


def option_expiry_for_day(day: str) -> str:
    parsed = date.fromisoformat(day)
    for expiry in OPTION_EXPIRIES:
        if parsed <= date.fromisoformat(expiry):
            return expiry
    raise ValueError(f"no frozen option expiry covers {day}")


def _assert_exact_sessions(payload: dict[str, Any], label: str) -> None:
    actual = list(payload.get("session_dates") or [])
    if actual != SESSION_DATES:
        raise ValueError(f"{label} session_dates do not exactly match frozen Cohort 2")


def validate_market(payload: dict[str, Any]) -> dict[str, Any]:
    _assert_exact_sessions(payload, "market")
    rows = list(payload.get("canonical_market_rows") or [])
    if len(rows) != EXPECTED["five_minute_rows"]:
        raise ValueError(f"market expected 5400 rows, got {len(rows)}")
    by_day: dict[str, int] = {day: 0 for day in SESSION_DATES}
    wrong_contract = 0
    for row in rows:
        ts = datetime.fromisoformat(str(row["timestamp"]))
        day = ts.date().isoformat()
        if day not in by_day:
            raise ValueError(f"market row outside frozen sessions: {day}")
        by_day[day] += 1
        expected_instrument = f"NIFTY FUT {futures_expiry_for_day(day)}"
        wrong_contract += str(row.get("futures_instrument")) != expected_instrument
        if row.get("futures_volume") is None or row.get("futures_open_interest") is None:
            raise ValueError(f"market row missing real futures volume/OI: {row['timestamp']}")
    if any(value != 75 for value in by_day.values()):
        raise ValueError("market does not contain exactly 75 bars per frozen session")
    if wrong_contract:
        raise ValueError(f"market has {wrong_contract} wrong-contract rows")
    return {"rows": len(rows), "sessions": len(by_day), "wrong_contract_rows": 0}


def validate_options(payload: dict[str, Any]) -> dict[str, Any]:
    _assert_exact_sessions(payload, "options")
    contract_by_date = payload.get("contract_by_date") or {}
    expected_contracts = {day: option_expiry_for_day(day) for day in SESSION_DATES}
    if contract_by_date != expected_contracts:
        raise ValueError("options contract_by_date does not match frozen weekly schedule")
    quality = payload.get("quality") or {}
    if quality.get("underlying_rows") != EXPECTED["five_minute_rows"]:
        raise ValueError("options underlying row count mismatch")
    if quality.get("duplicate_option_rows") != 0:
        raise ValueError("duplicate option rows")
    if quality.get("invalid_option_ohlc_rows") != 0:
        raise ValueError("invalid option OHLC rows")
    if quality.get("option_rows_missing_volume") != 0:
        raise ValueError("option rows missing volume")
    if quality.get("option_rows_missing_open_interest") != 0:
        raise ValueError("option rows missing open interest")
    if quality.get("atm_ce_pe_pair_coverage_pct") != 100.0:
        raise ValueError("ATM CE/PE coverage is not 100%")
    if quality.get("full_requested_band_coverage_pct") != 100.0:
        raise ValueError("full dynamic strike-band coverage is not 100%")
    if quality.get("failed_requests", 0) != 0:
        raise ValueError("option collector reported failed requests")
    return {
        "underlying_rows": quality["underlying_rows"],
        "option_rows": quality.get("option_rows"),
        "atm_coverage_pct": quality["atm_ce_pe_pair_coverage_pct"],
        "full_band_coverage_pct": quality["full_requested_band_coverage_pct"],
    }


def validate_auxiliary(payload: dict[str, Any], kind: str) -> dict[str, Any]:
    _assert_exact_sessions(payload, kind)
    quality = payload.get("quality") or {}
    expected_rows = EXPECTED["vix_rows"] if kind == "vix" else EXPECTED["spot_rows"]
    if quality.get("rows") != expected_rows:
        raise ValueError(f"{kind} expected {expected_rows} rows, got {quality.get('rows')}")
    if quality.get("duplicate_rows") != 0 or quality.get("invalid_ohlc_rows") != 0:
        raise ValueError(f"{kind} QA failed")
    return {"rows": quality["rows"], "sessions": quality.get("sessions")}


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect or validate frozen Development Cohort 2")
    parser.add_argument("--print-protocol", action="store_true")
    parser.add_argument("--market", type=Path)
    parser.add_argument("--options", type=Path)
    parser.add_argument("--vix", type=Path)
    parser.add_argument("--spot", type=Path)
    args = parser.parse_args()
    if args.print_protocol:
        print(json.dumps(PROTOCOL, indent=2))
        return
    results: dict[str, Any] = {}
    for kind in ("market", "options", "vix", "spot"):
        path = getattr(args, kind)
        if not path:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if kind == "market":
            results[kind] = validate_market(payload)
        elif kind == "options":
            results[kind] = validate_options(payload)
        else:
            results[kind] = validate_auxiliary(payload, kind)
    if not results:
        raise SystemExit("provide --print-protocol or at least one dataset")
    print(json.dumps({"protocol_version": PROTOCOL_VERSION, "validated": results}, indent=2))


if __name__ == "__main__":
    main()
