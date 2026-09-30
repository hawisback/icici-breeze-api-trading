from datetime import date, datetime, timedelta
from pathlib import Path
import json

import pytest

import services.historical.independent_older_market_structure_findings as findings
import services.historical.independent_older_market_structure_market as market
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
        contracts.append({"month": cursor.strftime("%Y-%m"), "expiry": expiry.isoformat()})
        cursor = (
            date(cursor.year + 1, 1, 1)
            if cursor.month == 12
            else date(cursor.year, cursor.month + 1, 1)
        )
    return {
        "protocol_version": PROTOCOL_VERSION,
        "provider": "BREEZE",
        "complete": True,
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


def test_manifest_loader_requires_complete_36_month_manifest(tmp_path: Path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(_manifest_payload()), encoding="utf-8")
    payload, mapping = market._load_manifest(path)
    assert payload["complete"] is True
    assert len(mapping) == 37
    assert sorted(mapping)[0] == "2022-01"
    assert sorted(mapping)[-1] == "2025-01"


def test_contract_periods_are_contiguous_from_frozen_window():
    mapping = {
        row["month"]: date.fromisoformat(row["expiry"])
        for row in _manifest_payload()["contracts"]
    }
    periods = market._contract_periods(mapping)
    assert periods[0][0] == date(2022, 1, 1)
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
