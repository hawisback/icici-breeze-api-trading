from services.historical.independent_cohort3_collection_audit import audit
from services.historical.independent_cohort3_protocol import (
    EXPECTED,
    OPTION_EXPIRIES,
    SESSION_DATES,
)


def _market():
    rows = [{"timestamp": "2026-01-02T09:15:00"}] * EXPECTED["five_minute_rows"]
    return {
        "session_dates": list(SESSION_DATES),
        "canonical_market_rows": rows,
        "coverage": {"futures_dates": list(SESSION_DATES)},
        "provenance": {
            "nifty_futures": {
                "canonical_source": "BREEZE",
                "volume_semantics": "actual futures traded volume",
                "open_interest_semantics": "provider-reported futures open interest",
            }
        },
    }


def _aux():
    return {
        "session_dates": list(SESSION_DATES),
        "quality": {
            "sessions": EXPECTED["sessions"],
            "rows": EXPECTED["five_minute_rows"],
            "duplicate_rows": 0,
            "invalid_ohlc_rows": 0,
            "complete_75_bar_sessions": EXPECTED["sessions"],
            "failed_requests": 0,
        },
    }


def _options():
    return {
        "session_dates": list(SESSION_DATES),
        "selection_policy": {
            "option_expiries_explicit": list(OPTION_EXPIRIES),
            "contract_stitching": False,
        },
        "quality": {
            "underlying_rows": EXPECTED["five_minute_rows"],
            "atm_ce_pe_pair_rows": EXPECTED["five_minute_rows"],
            "duplicate_option_rows": 0,
            "invalid_option_ohlc_rows": 0,
            "failed_requests": 0,
        },
    }


def _intrabar():
    return {
        "session_dates": list(SESSION_DATES),
        "quality": {
            "sessions": EXPECTED["sessions"],
            "rows": EXPECTED["one_minute_rows"],
            "duplicate_rows": 0,
            "invalid_ohlc_rows": 0,
            "wrong_contract_rows": 0,
            "complete_375_bar_sessions": EXPECTED["sessions"],
            "failed_requests": 0,
        },
    }


def test_collection_audit_keeps_cohort3_development_only():
    report = audit(_market(), _aux(), _options(), _aux(), _intrabar())
    assert report["validation"] == "PASSED"
    assert report["research_only"] is True
    assert report["candidate_frozen"] is False
    assert report["blind_data_used"] is False
    assert report["implementation_allowed"] is False
    assert report["cohort_role"] == "NEW_INSPECTED_DEVELOPMENT_NOT_BLIND_VALIDATION"
