import pytest

from services.historical.independent_futures_intrabar_research import (
    _contract_by_date,
    _pairs,
)


def test_intrabar_collector_derives_exact_canonical_contract_identity():
    payload = {
        "session_dates": ["2026-05-25", "2026-05-26", "2026-05-27"],
        "underlying_market_rows": [
            {"timestamp": "2026-05-25T09:15:00+05:30", "futures_instrument": "NIFTY FUT 2026-05-26"},
            {"timestamp": "2026-05-26T09:15:00+05:30", "futures_instrument": "NIFTY FUT 2026-05-26"},
            {"timestamp": "2026-05-27T09:15:00+05:30", "futures_instrument": "NIFTY FUT 2026-06-30"},
        ],
    }
    assert _contract_by_date(payload) == {
        "2026-05-25": "2026-05-26",
        "2026-05-26": "2026-05-26",
        "2026-05-27": "2026-06-30",
    }


def test_intrabar_collector_rejects_mixed_contract_on_same_session():
    payload = {
        "session_dates": ["2026-05-25"],
        "underlying_market_rows": [
            {"timestamp": "2026-05-25T09:15:00+05:30", "futures_instrument": "NIFTY FUT 2026-05-26"},
            {"timestamp": "2026-05-25T09:20:00+05:30", "futures_instrument": "NIFTY FUT 2026-06-30"},
        ],
    }
    with pytest.raises(ValueError):
        _contract_by_date(payload)


def test_intrabar_requests_are_limited_to_two_frozen_sessions_per_chunk():
    assert _pairs(["a", "b", "c", "d", "e"]) == [["a", "b"], ["c", "d"], ["e"]]
