from datetime import date

import services.historical.independent_older_nifty_expiry_manifest as manifest


class FakeClient:
    def __init__(self, valid):
        self.valid = valid

    def get_historical_data_v2(self, **kwargs):
        expiry = kwargs["expiry_date"][:10]
        month = expiry[:7]
        if self.valid.get(month) == expiry:
            return {
                "Status": 200,
                "Error": None,
                "Success": [{"datetime": f"{expiry} 09:15:00"}],
            }
        return {"Status": 200, "Error": None, "Success": []}


def test_candidates_start_at_last_thursday():
    assert manifest._expiry_candidates(2024, 1) == [
        date(2024, 1, 25),
        date(2024, 1, 24),
        date(2024, 1, 23),
        date(2024, 1, 22),
        date(2024, 1, 19),
    ]


def test_holiday_shifted_expiry_can_be_discovered(monkeypatch):
    monkeypatch.setattr(manifest, "_month_iter", lambda start, end: [(2024, 1)])
    report = manifest.discover_manifest(
        FakeClient({"2024-01": "2024-01-24"}),
        sleep_seconds=0,
    )
    assert report["complete"] is True
    assert report["months_resolved"] == 1
    assert report["contracts"][0]["expiry"] == "2024-01-24"
    assert [row["expiry"] for row in report["probe_diagnostics"][0]["attempts"]] == [
        "2024-01-25",
        "2024-01-24",
    ]


def test_unresolved_month_is_reported_without_pattern_scoring(monkeypatch):
    monkeypatch.setattr(manifest, "_month_iter", lambda start, end: [(2023, 8)])
    report = manifest.discover_manifest(FakeClient({}), sleep_seconds=0)
    assert report["complete"] is False
    assert report["unresolved_months"] == ["2023-08"]
    assert report["outcome_values_stored"] is False
    assert report["pattern_scoring_performed"] is False
