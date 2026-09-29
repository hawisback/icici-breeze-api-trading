"""QA-only amendment to Development Cohort 4 after provider data failure.

Original Cohort-4 dates were frozen before collection. During pre-outcome market
QA, 2025-06-27 was found to contain an irreconcilable nonpositive futures-volume
bar from Breeze at both 5-minute and 1-minute granularity.

This amendment is based only on data validity, before strategy outcome use:
- remove 2025-06-27;
- add the immediately preceding eligible F&O trading session outside the
  original cohort, 2025-05-15;
- preserve exactly 80 sessions;
- use Breeze only for the replacement session;
- do not inspect strategy outcomes when applying the substitution.

NSE/FAOP/65588 lists May 1 as the only May 2025 F&O trading holiday, so
2025-05-15 is an eligible Thursday trading session.
"""
from __future__ import annotations

from services.historical.independent_cohort4_protocol import (
    FUTURES_MONTHLY_EXPIRIES,
    OPTION_EXPIRIES,
    SESSION_DATES as ORIGINAL_SESSION_DATES,
)

AMENDMENT_VERSION = "DEVELOPMENT_COHORT_4_QA_SUBSTITUTION_V1"
ORIGINAL_MARKET_SHA256 = (
    "93af661d9b31c77abdbf9ce38e2250c42488ff40e715347f4fb34e21955db500"
)
FAILED_REPAIR_SHA256 = (
    "e87ae99cd56d26e3ddd39469f9cda32cfa7efc60ac9802c3e62d99a8d11a9803"
)

REMOVED_SESSION = "2025-06-27"
REPLACEMENT_SESSION = "2025-05-15"
REPLACEMENT_FUTURES_EXPIRY = "2025-05-29"

SESSION_DATES = sorted(
    [day for day in ORIGINAL_SESSION_DATES if day != REMOVED_SESSION]
    + [REPLACEMENT_SESSION]
)

EXPECTED = {
    "sessions": 80,
    "five_minute_rows": 6000,
    "five_minute_bars_per_session": 75,
}

GUARDRAILS = {
    "research_only": True,
    "candidate_frozen": False,
    "blind_data_used": False,
    "implementation_allowed": False,
    "qa_only_substitution": True,
    "substitution_reason": "irreconcilable_provider_volume_invalidity",
    "strategy_outcomes_not_used_for_substitution": True,
    "replacement_rule": (
        "immediately preceding eligible F&O trading session outside original cohort"
    ),
    "authorized_provider_for_replacement": "BREEZE",
    "new_external_broker_api_allowed_without_explicit_consent": False,
    "no_abs_volume_transform": True,
    "no_interpolation": True,
    "no_forward_fill": True,
    "no_contract_substitution": True,
    "no_candidate_promotion_from_cohort4_alone": True,
    "strategy_d_remains_paused": True,
}


def validate_amended_dates() -> None:
    if len(SESSION_DATES) != EXPECTED["sessions"]:
        raise ValueError("amended Cohort 4 must contain exactly 80 sessions")
    if len(set(SESSION_DATES)) != EXPECTED["sessions"]:
        raise ValueError("amended Cohort 4 contains duplicate sessions")
    if REMOVED_SESSION in SESSION_DATES:
        raise ValueError("invalid provider session remains in amended Cohort 4")
    if REPLACEMENT_SESSION not in SESSION_DATES:
        raise ValueError("replacement session missing from amended Cohort 4")
    if SESSION_DATES[0] != REPLACEMENT_SESSION:
        raise ValueError("replacement session must be earliest amended session")
    if SESSION_DATES[-1] != "2025-09-08":
        raise ValueError("amended Cohort 4 end date changed")



def expected_contract_by_date() -> dict[str, str]:
    result: dict[str, str] = {}
    for day in SESSION_DATES:
        matches = [expiry for expiry in FUTURES_MONTHLY_EXPIRIES if expiry >= day]
        if not matches:
            raise ValueError(f"no frozen futures expiry covers amended date {day}")
        result[day] = matches[0]
    return result


def validate_amended_market(payload: dict) -> None:
    validate_amended_dates()
    if list(payload.get("session_dates") or []) != SESSION_DATES:
        raise ValueError("market sessions differ from QA-amended Cohort 4 dates")
    rows = list(payload.get("nifty_futures") or [])
    if len(rows) != EXPECTED["five_minute_rows"]:
        raise ValueError(
            f"expected {EXPECTED['five_minute_rows']} futures rows, got {len(rows)}"
        )
    coverage = payload.get("coverage") or {}
    if list(coverage.get("futures_dates") or []) != SESSION_DATES:
        raise ValueError("amended futures coverage dates do not match")
    provenance = (payload.get("provenance") or {}).get("nifty_futures") or {}
    if provenance.get("canonical_source") != "BREEZE":
        raise ValueError("amended Cohort 4 canonical futures must remain BREEZE")
    if provenance.get("volume_semantics") != "actual futures traded volume":
        raise ValueError("amended Cohort 4 requires actual futures volume")
    if provenance.get("open_interest_semantics") != (
        "provider-reported futures open interest"
    ):
        raise ValueError("amended Cohort 4 requires provider-reported futures OI")
    if dict(provenance.get("breeze_contract_by_date") or {}) != expected_contract_by_date():
        raise ValueError("amended contract map differs from frozen schedule")

    quality = payload.get("quality") or {}
    required = {
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
    for key, expected in required.items():
        if quality.get(key) != expected:
            raise ValueError(
                f"amended market quality {key} expected {expected}, "
                f"got {quality.get(key)}"
            )
