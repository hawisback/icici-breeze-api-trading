"""Frozen protocol for independent NIFTY Development Cohort 3.

This cohort is deliberately created after the existing 152-session corpus has
been repeatedly inspected. It is a NEW DEVELOPMENT cohort, not blind validation.
Its dates and contract schedules are frozen before its market/option/intrabar
outcomes are collected or inspected.

Calendar basis:
- NSE/FAOP/71777 (12-Dec-2025): 2026 F&O trading holidays.
- NSE/FAOP/72262 (12-Jan-2026): adds 15-Jan-2026 F&O holiday.
- NSE NIFTY contract specification: Tuesday expiry; if Tuesday is a trading
  holiday, expiry is the previous trading day.
"""
from __future__ import annotations

from typing import Any

PROTOCOL_VERSION = "DEVELOPMENT_COHORT_3_V1"
COHORT_ROLE = "NEW_INSPECTED_DEVELOPMENT_NOT_BLIND_VALIDATION"

SESSION_DATES = [
    "2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07",
    "2026-01-08", "2026-01-09", "2026-01-12", "2026-01-13",
    "2026-01-14", "2026-01-16", "2026-01-19", "2026-01-20",
    "2026-01-21", "2026-01-22", "2026-01-23", "2026-01-27",
    "2026-01-28", "2026-01-29", "2026-01-30", "2026-02-02",
    "2026-02-03", "2026-02-04", "2026-02-05", "2026-02-06",
    "2026-02-09", "2026-02-10", "2026-02-11", "2026-02-12",
    "2026-02-13", "2026-02-16", "2026-02-17", "2026-02-18",
    "2026-02-19", "2026-02-20", "2026-02-23", "2026-02-24",
    "2026-02-25", "2026-02-26", "2026-02-27", "2026-03-02",
    "2026-03-04", "2026-03-05", "2026-03-06", "2026-03-09",
    "2026-03-10", "2026-03-11", "2026-03-12", "2026-03-13",
    "2026-03-16", "2026-03-17", "2026-03-18", "2026-03-19",
    "2026-03-20", "2026-03-23", "2026-03-24", "2026-03-25",
    "2026-03-27", "2026-03-30", "2026-04-01", "2026-04-02",
    "2026-04-06", "2026-04-07", "2026-04-08", "2026-04-09",
    "2026-04-10", "2026-04-13", "2026-04-15", "2026-04-16",
    "2026-04-17", "2026-04-20", "2026-04-21", "2026-04-22",
    "2026-04-23", "2026-04-24", "2026-04-27", "2026-04-28",
    "2026-04-29", "2026-04-30", "2026-05-04", "2026-05-05",
]

# Near-month NIFTY futures expiries needed to cover every frozen session.
FUTURES_MONTHLY_EXPIRIES = [
    "2026-01-27",
    "2026-02-24",
    "2026-03-30",  # 31-Mar is an F&O holiday.
    "2026-04-28",
    "2026-05-26",
]

# Nearest NIFTY option expiry on/after each session. Holiday Tuesdays are
# shifted to the previous trading day.
OPTION_EXPIRIES = [
    "2026-01-06", "2026-01-13", "2026-01-20", "2026-01-27",
    "2026-02-03", "2026-02-10", "2026-02-17", "2026-02-24",
    "2026-03-02", "2026-03-10", "2026-03-17", "2026-03-24",
    "2026-03-30",
    "2026-04-07", "2026-04-13", "2026-04-21", "2026-04-28",
    "2026-05-05",
]

EXPECTED = {
    "sessions": 80,
    "five_minute_rows": 6000,
    "one_minute_rows": 30000,
    "five_minute_bars_per_session": 75,
    "one_minute_bars_per_session": 375,
    "block_size_sessions": 10,
    "blocks": 8,
}

GUARDRAILS = {
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "role": COHORT_ROLE,
    "dates_frozen_before_collection": True,
    "futures_expiries_frozen_before_collection": True,
    "option_expiries_frozen_before_collection": True,
    "no_existing_hypothesis_may_be_relabelled_as_blind": True,
    "no_candidate_promotion_from_cohort3_alone": True,
    "strategy_d_remains_paused": True,
}


def validate_market(payload: dict[str, Any]) -> None:
    if list(payload.get("session_dates") or []) != SESSION_DATES:
        raise ValueError("market sessions differ from frozen Cohort 3 protocol")
    rows = list(payload.get("canonical_market_rows") or [])
    if len(rows) != EXPECTED["five_minute_rows"]:
        raise ValueError(
            f"expected {EXPECTED['five_minute_rows']} canonical market rows, "
            f"got {len(rows)}"
        )
    futures_dates = list((payload.get("coverage") or {}).get("futures_dates") or [])
    if futures_dates != SESSION_DATES:
        raise ValueError("futures coverage does not exactly match Cohort 3 sessions")
    provenance = (payload.get("provenance") or {}).get("nifty_futures") or {}
    if provenance.get("canonical_source") != "BREEZE":
        raise ValueError("Cohort 3 canonical futures must come from BREEZE")
    if provenance.get("volume_semantics") != "actual futures traded volume":
        raise ValueError("Cohort 3 requires actual futures traded volume")
    if provenance.get("open_interest_semantics") != (
        "provider-reported futures open interest"
    ):
        raise ValueError("Cohort 3 requires provider-reported futures OI")


def validate_options(payload: dict[str, Any]) -> None:
    if list(payload.get("session_dates") or []) != SESSION_DATES:
        raise ValueError("option sessions differ from frozen Cohort 3 protocol")
    policy = payload.get("selection_policy") or {}
    if list(policy.get("option_expiries_explicit") or []) != OPTION_EXPIRIES:
        raise ValueError("option expiry schedule differs from frozen Cohort 3 protocol")
    if policy.get("contract_stitching") is not False:
        raise ValueError("option contract stitching must remain disabled")
    quality = payload.get("quality") or {}
    if quality.get("underlying_rows") != EXPECTED["five_minute_rows"]:
        raise ValueError("option underlying row count changed")
    if quality.get("atm_ce_pe_pair_rows") != EXPECTED["five_minute_rows"]:
        raise ValueError("ATM CE/PE coverage is incomplete")
    if quality.get("duplicate_option_rows") != 0:
        raise ValueError("duplicate option rows found")
    if quality.get("invalid_option_ohlc_rows") != 0:
        raise ValueError("invalid option OHLC rows found")
    if quality.get("failed_requests", 0) != 0:
        raise ValueError("option collection has failed requests")


def validate_auxiliary(payload: dict[str, Any], kind: str) -> None:
    if list(payload.get("session_dates") or []) != SESSION_DATES:
        raise ValueError(f"{kind} sessions differ from frozen Cohort 3 protocol")
    quality = payload.get("quality") or {}
    if quality.get("sessions") != EXPECTED["sessions"]:
        raise ValueError(f"{kind} session count changed")
    if quality.get("rows") != EXPECTED["five_minute_rows"]:
        raise ValueError(f"{kind} five-minute row count changed")
    if quality.get("duplicate_rows") != 0:
        raise ValueError(f"{kind} duplicate rows found")
    if quality.get("invalid_ohlc_rows") != 0:
        raise ValueError(f"{kind} invalid OHLC rows found")
    if quality.get("complete_75_bar_sessions") != EXPECTED["sessions"]:
        raise ValueError(f"{kind} does not have 75 bars for every session")
    if quality.get("failed_requests", 0) != 0:
        raise ValueError(f"{kind} collection has failed requests")


def validate_intrabar(payload: dict[str, Any]) -> None:
    if list(payload.get("session_dates") or []) != SESSION_DATES:
        raise ValueError("intrabar sessions differ from frozen Cohort 3 protocol")
    quality = payload.get("quality") or {}
    required = {
        "sessions": EXPECTED["sessions"],
        "rows": EXPECTED["one_minute_rows"],
        "duplicate_rows": 0,
        "invalid_ohlc_rows": 0,
        "wrong_contract_rows": 0,
        "complete_375_bar_sessions": EXPECTED["sessions"],
        "failed_requests": 0,
    }
    for key, expected in required.items():
        if quality.get(key) != expected:
            raise ValueError(
                f"intrabar quality {key} expected {expected}, got {quality.get(key)}"
            )


PROTOCOL: dict[str, Any] = {
    "protocol_version": PROTOCOL_VERSION,
    "cohort_role": COHORT_ROLE,
    "session_dates": SESSION_DATES,
    "futures_monthly_expiries": FUTURES_MONTHLY_EXPIRIES,
    "option_expiries": OPTION_EXPIRIES,
    "expected": EXPECTED,
    "guardrails": GUARDRAILS,
}
