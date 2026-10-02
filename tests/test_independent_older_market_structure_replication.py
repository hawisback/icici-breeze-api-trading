from datetime import date, datetime, timedelta
from pathlib import Path
import json

import pytest

import hashlib
import services.historical.independent_older_market_structure_findings as findings
import services.historical.independent_older_market_structure_market as market
from services.historical.independent_older_market_structure_qa_amendment import (
    OLDER_MARKET_STRUCTURE_QA_AMENDMENT_V1,
)
from services.historical.independent_older_market_structure_replication_protocol import (
    PROTOCOL_VERSION,
    RELATIONSHIPS,
    WINDOW,
)


def _manifest_payload():
    contracts = []
    cursor = date(2022, 1, 1)
    while cursor <= date(2025, 1, 1):
        # Synthetic manifest only: one expiry per month is enough for structural tests.
        expiry = date(cursor.year, cursor.month, 20)
        contracts.append({
            "month": cursor.strftime("%Y-%m"),
            "expiry": None if cursor == date(2022, 1, 1) else expiry.isoformat(),
        })
        cursor = (
            date(cursor.year + 1, 1, 1)
            if cursor.month == 12
            else date(cursor.year, cursor.month + 1, 1)
        )
    return {
        "protocol_version": PROTOCOL_VERSION,
        "provider": "BREEZE",
        "complete": False,
        "unresolved_months": ["2022-01"],
        "contracts": contracts,
    }


def test_protocol_freezes_only_two_preselected_relationships():
    assert set(RELATIONSHIPS) == {
        "opening_range_vs_remaining_range",
        "daily_range_persistence",
    }
    assert WINDOW["start"] == "2022-01-01"
    assert WINDOW["end"] == "2024-12-31"
    assert WINDOW["bars_per_session"] == 75
    assert OLDER_MARKET_STRUCTURE_QA_AMENDMENT_V1["effective_window_start"] == (
        "2022-02-01"
    )
    assert OLDER_MARKET_STRUCTURE_QA_AMENDMENT_V1["relationships_changed"] is False
    assert OLDER_MARKET_STRUCTURE_QA_AMENDMENT_V1["replication_gate_changed"] is False


def test_manifest_loader_applies_recorded_january_2022_qa_amendment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(_manifest_payload()), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setitem(
        OLDER_MARKET_STRUCTURE_QA_AMENDMENT_V1,
        "expiry_manifest_sha256",
        digest,
    )
    payload, mapping = market._load_manifest(path)
    assert payload["complete"] is False
    assert payload["unresolved_months"] == ["2022-01"]
    assert len(mapping) == 36
    assert sorted(mapping)[0] == "2022-02"
    assert sorted(mapping)[-1] == "2025-01"


def test_contract_periods_are_contiguous_from_frozen_window():
    mapping = {
        row["month"]: date.fromisoformat(row["expiry"])
        for row in _manifest_payload()["contracts"]
        if row["expiry"] is not None
    }
    periods = market._contract_periods(mapping)
    assert periods[0][0] == date(2022, 2, 1)
    for previous, current in zip(periods, periods[1:]):
        assert current[0] == previous[2] + timedelta(days=1)


def _synthetic_market_payload():
    rows = []
    all_days = []
    for year in (2022, 2023, 2024):
        start = date(year, 1, 3)
        cursor = start
        while len([d for d in all_days if d.year == year]) < 12:
            if cursor.weekday() < 5:
                all_days.append(cursor)
            cursor += timedelta(days=1)

    previous_scale = 1.0
    for index, day in enumerate(all_days):
        scale = 1.0 + index * 0.08
        opening_scale = scale
        remaining_scale = 1.2 + scale * 1.5
        start = datetime.fromisoformat(f"{day.isoformat()}T09:15:00+05:30")
        base = 20000.0 + index * 20.0
        close = base
        for i in range(75):
            width = opening_scale if i < 6 else remaining_scale
            open_px = close
            close = open_px + 0.2 * width
            rows.append(
                {
                    "timestamp": (start + timedelta(minutes=5 * i)).isoformat(),
                    "date": day.isoformat(),
                    "open": open_px,
                    "high": max(open_px, close) + width,
                    "low": min(open_px, close) - width,
                    "close": close,
                    "futures_expiry": f"{day.year}-12-29",
                    "source": "BREEZE",
                }
            )
        previous_scale = scale
    return {
        "protocol_version": PROTOCOL_VERSION,
        "provider": "BREEZE",
        "pattern_scoring_performed": False,
        "futures_rows": rows,
    }


def test_analyzer_can_pass_both_relationships_on_consistent_synthetic_history(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setitem(findings.REPLICATION_GATE, "minimum_complete_sessions", 30)
    monkeypatch.setitem(findings.BOOTSTRAP, "draws", 200)

    report = findings.analyze_market(_synthetic_market_payload())

    assert report["complete_sessions"] == 36
    assert report["decision"] == (
        "OLDER_HISTORICAL_BOTH_MARKET_STRUCTURE_RELATIONSHIPS_REPLICATED"
    )
    for result in report["results"].values():
        assert result["pooled_spearman"] > 0.0
        assert all(value > 0.0 for value in result["calendar_year_spearman"].values())
        assert result["session_cluster_bootstrap_95pct"][0] > 0.0
        assert result["replication_gate"]["passed"] is True


def test_market_validator_rejects_pre_scored_artifact():
    payload = _synthetic_market_payload()
    payload["pattern_scoring_performed"] = True
    with pytest.raises(ValueError, match="outcome-unscored"):
        findings._validate_market(payload)


class _FlakyHistoryClient:
    def __init__(self):
        self.calls = 0

    def get_historical_data_v2(self, **kwargs):
        self.calls += 1
        if self.calls < 3:
            return {"Status": 500, "Error": "temporary", "Success": []}
        return {"Status": 200, "Error": None, "Success": []}


def test_history_request_retries_provider_errors(monkeypatch: pytest.MonkeyPatch):
    client = _FlakyHistoryClient()
    monkeypatch.setattr(market.time_module, "sleep", lambda seconds: None)
    response = market._request_history(
        client,
        chunk_start=date(2022, 2, 1),
        chunk_end=date(2022, 2, 10),
        expiry=date(2022, 2, 24),
    )
    assert client.calls == 3
    assert response["Status"] == 200
