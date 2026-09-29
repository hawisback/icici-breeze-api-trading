import copy
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from services.historical.independent_cohort4_market_qa_substitution import (
    apply_substitution,
)
from services.historical.independent_cohort4_protocol import (
    SESSION_DATES as ORIGINAL_SESSION_DATES,
)
from services.historical.independent_cohort4_qa_amendment import (
    FAILED_REPAIR_SHA256,
    REMOVED_SESSION,
    REPLACEMENT_FUTURES_EXPIRY,
    REPLACEMENT_SESSION,
    SESSION_DATES,
    expected_contract_by_date,
)

IST = ZoneInfo("Asia/Kolkata")


def _rows(day, expiry, *, negative=False):
    start = datetime.fromisoformat(f"{day}T09:15:00+05:30")
    rows = []
    for index in range(75):
        ts = start + timedelta(minutes=5 * index)
        price = 25000.0 + index
        rows.append({
            "timestamp": ts.isoformat(),
            "open": price,
            "high": price + 1.0,
            "low": price - 1.0,
            "close": price + 0.25,
            "volume": -1000.0 if negative and index == 63 else 1000.0 + index,
            "open_interest": 500000.0 + index,
            "instrument": f"NIFTY FUT {expiry}",
            "source": "BREEZE",
            "instrument_type": "Futures",
        })
    return rows


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.last_history_debug = {}

    def history(self, session_dates, expiry_date):
        self.calls.append(([day.isoformat() for day in session_dates], expiry_date))
        self.last_history_debug = {
            "expiry_date": expiry_date,
            "normalized_count": len(self.rows),
        }
        return [
            {key: value for key, value in row.items() if key != "instrument_type"}
            for row in self.rows
        ]


def _payload():
    contract_map = {}
    futures = []
    for day in ORIGINAL_SESSION_DATES:
        expiry = next(
            expiry
            for expiry in ["2025-05-29","2025-06-26","2025-07-31","2025-08-28","2025-09-30"]
            if expiry >= day
        )
        contract_map[day] = expiry
        futures.extend(
            _rows(day, expiry, negative=(day == REMOVED_SESSION))
        )
    return {
        "research_type": "NIFTY_DEVELOPMENT_COHORT_4_MARKET_DATA_REPAIRED_V1",
        "research_only": True,
        "candidate_frozen": False,
        "blind_data_used": False,
        "implementation_allowed": False,
        "session_dates": list(ORIGINAL_SESSION_DATES),
        "nifty_futures": copy.deepcopy(futures),
        "canonical_market_rows": [],
        "provider_series": {"BREEZE": {"NIFTY_FUTURES": copy.deepcopy(futures)}},
        "provenance": {
            "nifty_futures": {
                "canonical_source": "BREEZE",
                "breeze_contract_by_date": contract_map,
                "volume_semantics": "actual futures traded volume",
                "open_interest_semantics": "provider-reported futures open interest",
            }
        },
        "coverage": {
            "nifty_futures_rows": 6000,
            "futures_dates": list(ORIGINAL_SESSION_DATES),
        },
        "credentialed_sources": {
            "BREEZE": {"instrument_config": {"contract_by_date": contract_map}}
        },
        "provider_quality": {
            "nifty_futures": {"providers": {"BREEZE": {}}}
        },
        "quality": {},
    }


def test_substitution_uses_only_one_breeze_replacement_request():
    replacement = _rows(REPLACEMENT_SESSION, REPLACEMENT_FUTURES_EXPIRY)
    client = FakeClient(replacement)
    report = apply_substitution(
        _payload(),
        client=client,
        input_sha256=FAILED_REPAIR_SHA256,
    )

    assert client.calls == [([REPLACEMENT_SESSION], REPLACEMENT_FUTURES_EXPIRY)]
    assert report["session_dates"] == SESSION_DATES
    assert REMOVED_SESSION not in report["session_dates"]
    assert REPLACEMENT_SESSION in report["session_dates"]
    assert report["quality"]["rows"] == 6000
    assert report["quality"]["nonpositive_volume_rows"] == 0
    assert report["quality"]["complete_75_bar_sessions"] == 80
    assert report["provenance"]["nifty_futures"]["breeze_contract_by_date"] == (
        expected_contract_by_date()
    )


def test_substitution_records_qa_only_no_external_provider_use():
    replacement = _rows(REPLACEMENT_SESSION, REPLACEMENT_FUTURES_EXPIRY)
    report = apply_substitution(
        _payload(),
        client=FakeClient(replacement),
        input_sha256=FAILED_REPAIR_SHA256,
    )
    qa = report["qa_amendment"]
    assert qa["strategy_outcomes_used"] is False
    assert qa["new_external_broker_api_used"] is False
    assert qa["synthetic_fill"] is False
    assert qa["abs_volume_transform"] is False
    assert qa["interpolation"] is False
    assert qa["forward_fill"] is False
