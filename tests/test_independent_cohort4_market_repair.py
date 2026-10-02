from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import services.historical.independent_cohort4_market_repair as repair_mod
from services.historical.independent_cohort4_market_repair import (
    _session_issue,
    repair_market,
)

IST = ZoneInfo("Asia/Kolkata")


def _rows(day: str, expiry: str, *, negative_index: int | None = None):
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
            "volume": -1000.0 if index == negative_index else 1000.0 + index,
            "open_interest": 500000.0 + index,
            "source": "BREEZE",
            "instrument": f"NIFTY FUT {expiry}",
            "instrument_type": "Futures",
        })
    return rows


class FakeClient:
    def __init__(self, good_rows_by_day):
        self.good_rows_by_day = good_rows_by_day
        self.calls = []

    def history(self, session_dates, expiry_date):
        assert len(session_dates) == 1
        day = session_dates[0].isoformat()
        self.calls.append((day, expiry_date))
        return [
            {
                key: value
                for key, value in row.items()
                if key != "instrument_type"
            }
            for row in self.good_rows_by_day[day]
        ]


def test_session_issue_rejects_negative_actual_futures_volume():
    rows = _rows("2025-06-27", "2025-07-31", negative_index=63)
    issues = _session_issue(
        rows, day="2025-06-27", expiry="2025-07-31"
    )
    assert "nonpositive_volume" in issues


def test_repair_refetches_only_deficient_session_without_fabrication(monkeypatch):
    session_dates = ["2025-06-26", "2025-06-27"]
    expected = {
        "sessions": 2,
        "five_minute_rows": 150,
        "one_minute_rows": 750,
        "five_minute_bars_per_session": 75,
        "one_minute_bars_per_session": 375,
        "block_size_sessions": 1,
        "blocks": 2,
    }
    monkeypatch.setattr(repair_mod, "SESSION_DATES", session_dates)
    monkeypatch.setattr(repair_mod, "EXPECTED", expected)
    monkeypatch.setattr(
        repair_mod, "FUTURES_MONTHLY_EXPIRIES", ["2025-06-26", "2025-07-31"]
    )
    monkeypatch.setattr(repair_mod, "validate_market", lambda _payload: None)

    good_26 = _rows("2025-06-26", "2025-06-26")
    bad_27 = _rows("2025-06-27", "2025-07-31", negative_index=63)
    good_27 = _rows("2025-06-27", "2025-07-31")
    all_rows = good_26 + bad_27
    contract_by_date = {
        "2025-06-26": "2025-06-26",
        "2025-06-27": "2025-07-31",
    }
    payload = {
        "research_type": "NIFTY_DEVELOPMENT_COHORT_4_MARKET_DATA_V1",
        "research_only": True,
        "candidate_frozen": False,
        "blind_data_used": False,
        "implementation_allowed": False,
        "session_dates": session_dates,
        "provenance": {
            "nifty_futures": {
                "breeze_contract_by_date": contract_by_date,
                "canonical_source": "BREEZE",
                "volume_semantics": "actual futures traded volume",
                "open_interest_semantics": "provider-reported futures open interest",
            }
        },
        "coverage": {
            "nifty_futures_rows": 150,
            "futures_dates": session_dates,
        },
        "provider_quality": {
            "nifty_futures": {"providers": {"BREEZE": {}}}
        },
        "provider_series": {"BREEZE": {"NIFTY_FUTURES": all_rows}},
        "nifty_futures": all_rows,
        "canonical_market_rows": [],
        "quality": {},
    }
    client = FakeClient({"2025-06-27": good_27})
    report = repair_market(
        payload,
        client=client,
        attempts_per_session=2,
        input_sha256="abc",
    )

    assert client.calls == [("2025-06-27", "2025-07-31")]
    assert report["repair"]["initial_deficient_sessions"] == {
        "2025-06-27": ["nonpositive_volume"]
    }
    assert report["repair"]["repaired_sessions"] == ["2025-06-27"]
    assert report["repair"]["remaining_deficient_sessions"] == {}
    assert report["repair"]["policy"]["synthetic_fill"] is False
    assert report["repair"]["policy"]["sign_correction"] is False
    assert report["quality"]["rows"] == 150
    assert report["quality"]["nonpositive_volume_rows"] == 0
    assert report["quality"]["complete_75_bar_sessions"] == 2
