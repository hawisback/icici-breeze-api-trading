import json
from pathlib import Path

from services.historical.independent_prospective_magnitude_collection_ledger import (
    GUARDRAILS,
    build_collection_ledger,
)
from services.historical.independent_prospective_magnitude_market import (
    SESSION_RESEARCH_TYPE,
    _expected_timestamps,
)
from services.historical.independent_prospective_magnitude_protocol import (
    CONTRACT_BY_DATE,
    EXPECTED_BARS_PER_SESSION,
    PROTOCOL_VERSION,
    SESSION_DATES,
)


def _session_payload(day: str) -> dict:
    expiry = CONTRACT_BY_DATE[day]
    rows = [
        {
            "timestamp": ts,
            "instrument": f"NIFTY FUT {expiry}",
            "futures_expiry": expiry,
            "source": "BREEZE",
            "open": 20000.0,
            "high": 20001.0,
            "low": 19999.0,
            "close": 20000.5,
            "volume": 1000,
            "open_interest": 50000,
        }
        for ts in _expected_timestamps(day)
    ]
    return {
        "research_type": SESSION_RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "research_only": True,
        "prospective_sample": True,
        "qa_only": True,
        "outcome_scoring_performed": False,
        "candidate_frozen": False,
        "blind_validation_opened": False,
        "implementation_allowed": False,
        "provider": "BREEZE",
        "session_date": day,
        "futures_expiry": expiry,
        "bars_per_session": EXPECTED_BARS_PER_SESSION,
        "rows": EXPECTED_BARS_PER_SESSION,
        "quality": {
            "rows": EXPECTED_BARS_PER_SESSION,
            "complete_77_bar_session": True,
        },
        "futures_rows": rows,
    }


def test_empty_ledger_reports_all_frozen_dates_missing(tmp_path: Path):
    report = build_collection_ledger(tmp_path)

    assert report["expected_sessions"] == 30
    assert report["valid_collected_sessions"] == 0
    assert report["invalid_collected_sessions"] == 0
    assert report["missing_sessions"] == 30
    assert report["collection_complete"] is False
    assert report["missing_session_dates"] == SESSION_DATES
    assert report["next_missing_session_date"] == SESSION_DATES[0]


def test_valid_session_is_sha_ledgered_without_outcome_fields(tmp_path: Path):
    day = SESSION_DATES[0]
    path = tmp_path / f"{day}.json"
    path.write_text(json.dumps(_session_payload(day)), encoding="utf-8")

    report = build_collection_ledger(tmp_path)

    assert report["valid_collected_sessions"] == 1
    assert report["invalid_collected_sessions"] == 0
    assert report["missing_sessions"] == 29
    artifact = report["valid_artifacts"][0]
    assert artifact["date"] == day
    assert artifact["provider"] == "BREEZE"
    assert artifact["futures_expiry"] == CONTRACT_BY_DATE[day]
    assert artifact["rows"] == 77
    assert artifact["qa_only"] is True
    assert artifact["outcome_scoring_performed"] is False
    assert len(artifact["sha256"]) == 64

    text = json.dumps(report).lower()
    assert "model_forecast" not in text
    assert "spearman" not in text
    assert "target_value" not in text
    assert "predictor_value" not in text


def test_scored_or_malformed_session_is_invalid_not_valid(tmp_path: Path):
    day = SESSION_DATES[0]
    payload = _session_payload(day)
    payload["outcome_scoring_performed"] = True
    (tmp_path / f"{day}.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    report = build_collection_ledger(tmp_path)

    assert report["valid_collected_sessions"] == 0
    assert report["invalid_collected_sessions"] == 1
    assert report["collection_complete"] is False
    assert report["invalid_artifacts"][0]["date"] == day
    assert "outcome scoring" in report["invalid_artifacts"][0]["error"]


def test_extra_json_is_ignored_and_reported(tmp_path: Path):
    (tmp_path / "notes.json").write_text("{}", encoding="utf-8")
    report = build_collection_ledger(tmp_path)
    assert report["extra_json_files_ignored"] == ["notes.json"]


def test_collection_ledger_preserves_research_firewall():
    assert GUARDRAILS["qa_only"] is True
    assert GUARDRAILS["outcome_scoring_performed"] is False
    assert GUARDRAILS["predictor_computed"] is False
    assert GUARDRAILS["target_computed"] is False
    assert GUARDRAILS["correlation_computed"] is False
    assert GUARDRAILS["pnl_scored"] is False
    assert GUARDRAILS["model_fitting"] is False
    assert GUARDRAILS["implementation_allowed"] is False
    assert GUARDRAILS["strategy_d_remains_paused"] is True
