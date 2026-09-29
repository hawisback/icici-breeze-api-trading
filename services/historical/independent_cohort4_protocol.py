"""Frozen protocol for independent NIFTY Development Cohort 4.

This cohort is created only after the corrected 232-session V2 corpus and its
externally motivated opening-to-closing intraday-momentum study were inspected.
It is NEW INSPECTED DEVELOPMENT data, never blind validation.

Calendar/contract basis frozen before collection:
- NSE/FAOP/65588 (13-Dec-2024): 2025 F&O trading holidays. Within this
  16-May-2025 through 08-Sep-2025 window, 15-Aug and 27-Aug are trading
  holidays; 05-Sep is not an F&O trading holiday.
- NSE/FAOP/67338 (27-Mar-2025): deferred the proposed Monday-expiry change.
- NSE/FAOP/68747 (25-Jun-2025): expiries falling on/before 31-Aug-2025 remain
  unchanged; newly generated September-and-later NIFTY expiries use Tuesday.
  The annexure explicitly lists 02-Sep-2025 and 09-Sep-2025 weekly expiries.

The cohort ends on 08-Sep-2025, immediately before Development Cohort 2 starts
on 09-Sep-2025, so there is no session overlap.
"""
from __future__ import annotations

from typing import Any

PROTOCOL_VERSION = "DEVELOPMENT_COHORT_4_V1"
COHORT_ROLE = "NEW_INSPECTED_DEVELOPMENT_NOT_BLIND_VALIDATION"

SESSION_DATES = [
    "2025-05-16",
    "2025-05-19",
    "2025-05-20",
    "2025-05-21",
    "2025-05-22",
    "2025-05-23",
    "2025-05-26",
    "2025-05-27",
    "2025-05-28",
    "2025-05-29",
    "2025-05-30",
    "2025-06-02",
    "2025-06-03",
    "2025-06-04",
    "2025-06-05",
    "2025-06-06",
    "2025-06-09",
    "2025-06-10",
    "2025-06-11",
    "2025-06-12",
    "2025-06-13",
    "2025-06-16",
    "2025-06-17",
    "2025-06-18",
    "2025-06-19",
    "2025-06-20",
    "2025-06-23",
    "2025-06-24",
    "2025-06-25",
    "2025-06-26",
    "2025-06-27",
    "2025-06-30",
    "2025-07-01",
    "2025-07-02",
    "2025-07-03",
    "2025-07-04",
    "2025-07-07",
    "2025-07-08",
    "2025-07-09",
    "2025-07-10",
    "2025-07-11",
    "2025-07-14",
    "2025-07-15",
    "2025-07-16",
    "2025-07-17",
    "2025-07-18",
    "2025-07-21",
    "2025-07-22",
    "2025-07-23",
    "2025-07-24",
    "2025-07-25",
    "2025-07-28",
    "2025-07-29",
    "2025-07-30",
    "2025-07-31",
    "2025-08-01",
    "2025-08-04",
    "2025-08-05",
    "2025-08-06",
    "2025-08-07",
    "2025-08-08",
    "2025-08-11",
    "2025-08-12",
    "2025-08-13",
    "2025-08-14",
    "2025-08-18",
    "2025-08-19",
    "2025-08-20",
    "2025-08-21",
    "2025-08-22",
    "2025-08-25",
    "2025-08-26",
    "2025-08-28",
    "2025-08-29",
    "2025-09-01",
    "2025-09-02",
    "2025-09-03",
    "2025-09-04",
    "2025-09-05",
    "2025-09-08"
]

FUTURES_MONTHLY_EXPIRIES = [
    "2025-05-29",
    "2025-06-26",
    "2025-07-31",
    "2025-08-28",
    "2025-09-30",
]

OPTION_EXPIRIES = [
    "2025-05-22", "2025-05-29",
    "2025-06-05", "2025-06-12", "2025-06-19", "2025-06-26",
    "2025-07-03", "2025-07-10", "2025-07-17", "2025-07-24", "2025-07-31",
    "2025-08-07", "2025-08-14", "2025-08-21", "2025-08-28",
    "2025-09-02", "2025-09-09",
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
    "no_overlap_with_cohort2": True,
    "no_existing_hypothesis_may_be_relabelled_as_blind": True,
    "no_candidate_promotion_from_cohort4_alone": True,
    "fresh_blind_data_used": False,
    "strategy_d_remains_paused": True,
}


def validate_market(payload: dict[str, Any]) -> None:
    if list(payload.get("session_dates") or []) != SESSION_DATES:
        raise ValueError("market sessions differ from frozen Cohort 4 protocol")
    rows = list(payload.get("canonical_market_rows") or [])
    if len(rows) != EXPECTED["five_minute_rows"]:
        raise ValueError(
            f"expected {EXPECTED['five_minute_rows']} canonical market rows, "
            f"got {len(rows)}"
        )
    futures_dates = list((payload.get("coverage") or {}).get("futures_dates") or [])
    if futures_dates != SESSION_DATES:
        raise ValueError("futures coverage does not exactly match Cohort 4 sessions")
    provenance = (payload.get("provenance") or {}).get("nifty_futures") or {}
    if provenance.get("canonical_source") != "BREEZE":
        raise ValueError("Cohort 4 canonical futures must come from BREEZE")
    if provenance.get("volume_semantics") != "actual futures traded volume":
        raise ValueError("Cohort 4 requires actual futures traded volume")
    if provenance.get("open_interest_semantics") != (
        "provider-reported futures open interest"
    ):
        raise ValueError("Cohort 4 requires provider-reported futures OI")
    quality = payload.get("quality") or {}
    required_quality = {
        "sessions": EXPECTED["sessions"],
        "rows": EXPECTED["five_minute_rows"],
        "duplicate_rows": 0,
        "invalid_ohlc_rows": 0,
        "missing_volume_rows": 0,
        "missing_open_interest_rows": 0,
        "nonpositive_volume_rows": 0,
        "nonpositive_open_interest_rows": 0,
        "wrong_contract_rows": 0,
        "complete_75_bar_sessions": EXPECTED["sessions"],
    }
    for key, expected in required_quality.items():
        if quality.get(key) != expected:
            raise ValueError(
                f"market quality {key} expected {expected}, got {quality.get(key)}"
            )


def validate_options(payload: dict[str, Any]) -> None:
    if list(payload.get("session_dates") or []) != SESSION_DATES:
        raise ValueError("option sessions differ from frozen Cohort 4 protocol")
    policy = payload.get("selection_policy") or {}
    if list(policy.get("option_expiries_explicit") or []) != OPTION_EXPIRIES:
        raise ValueError("option expiry schedule differs from frozen Cohort 4 protocol")
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
        raise ValueError(f"{kind} sessions differ from frozen Cohort 4 protocol")
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
        raise ValueError("intrabar sessions differ from frozen Cohort 4 protocol")
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
