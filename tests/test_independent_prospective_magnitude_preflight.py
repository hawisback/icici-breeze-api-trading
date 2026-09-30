import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import services.historical.independent_prospective_magnitude_preflight as preflight_module
from services.historical.independent_prospective_magnitude_market import (
    SESSION_RESEARCH_TYPE,
    _expected_timestamps,
)
from services.historical.independent_prospective_magnitude_protocol import (
    CONTRACT_BY_DATE,
    PROTOCOL_VERSION,
)

IST = ZoneInfo("Asia/Kolkata")


def _valid_session_payload(day: str) -> dict:
    expiry = CONTRACT_BY_DATE[day]
    rows = [
        {
            "timestamp": ts,
            "open": 25000.0,
            "high": 25001.0,
            "low": 24999.0,
            "close": 25000.5,
            "volume": 1000.0,
            "open_interest": 100000.0,
            "instrument": f"NIFTY FUT {expiry}",
            "futures_expiry": expiry,
            "source": "BREEZE",
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
        "session_start": "09:15",
        "session_end_exclusive": "15:40",
        "bars_per_session": 77,
        "rows": 77,
        "quality": {
            "rows": 77,
            "complete_77_bar_session": True,
            "duplicate_rows": 0,
            "invalid_ohlc_rows": 0,
            "missing_volume_rows": 0,
            "missing_open_interest_rows": 0,
            "nonpositive_volume_rows": 0,
            "nonpositive_open_interest_rows": 0,
            "wrong_contract_rows": 0,
        },
        "request_diagnostics": {
            "date": day,
            "futures_expiry": expiry,
            "status": 200,
            "error": None,
            "raw_count": 77,
            "accepted_count": 77,
        },
        "futures_rows": rows,
    }


def _set_ready_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BREEZE_API_KEY", "key")
    monkeypatch.setenv("BREEZE_SECRET_KEY", "secret")
    monkeypatch.setenv("BREEZE_SESSION_TOKEN", "token")
    monkeypatch.setattr(
        preflight_module.importlib.util,
        "find_spec",
        lambda name: object() if name == "breeze_connect" else None,
    )


def test_preflight_reports_time_lock_before_session_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _set_ready_environment(monkeypatch)
    report = preflight_module.preflight(
        "2026-10-01",
        session_dir=tmp_path,
        now=datetime(2026, 10, 1, 15, 39, tzinfo=IST),
        load_env=False,
    )

    assert report["collection_window_open"] is False
    assert report["safe_to_attempt_collection"] is False
    assert report["blockers"] == ["collection_window_not_open"]
    assert report["ledger_summary"]["valid_collected_sessions"] == 0
    assert report["ledger_summary"]["missing_sessions"] == 30


def test_preflight_is_ready_after_close_when_local_dependencies_are_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _set_ready_environment(monkeypatch)
    report = preflight_module.preflight(
        "2026-10-01",
        session_dir=tmp_path,
        now=datetime(2026, 10, 1, 15, 40, tzinfo=IST),
        load_env=False,
    )

    assert report["collection_window_open"] is True
    assert report["credential_presence"] == {
        "BREEZE_API_KEY": True,
        "BREEZE_SECRET_KEY": True,
        "BREEZE_SESSION_TOKEN": True,
    }
    assert report["breeze_connect_installed"] is True
    assert report["session_artifact"]["status"] == "absent"
    assert report["safe_to_attempt_collection"] is True
    assert report["blockers"] == []


def test_preflight_blocks_missing_credentials_without_exposing_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("BREEZE_API_KEY", raising=False)
    monkeypatch.delenv("BREEZE_SECRET_KEY", raising=False)
    monkeypatch.delenv("BREEZE_SESSION_TOKEN", raising=False)
    monkeypatch.setattr(
        preflight_module.importlib.util,
        "find_spec",
        lambda name: object(),
    )

    report = preflight_module.preflight(
        "2026-10-01",
        session_dir=tmp_path,
        now=datetime(2026, 10, 1, 15, 40, tzinfo=IST),
        load_env=False,
    )

    assert report["credential_presence"] == {
        "BREEZE_API_KEY": False,
        "BREEZE_SECRET_KEY": False,
        "BREEZE_SESSION_TOKEN": False,
    }
    assert "missing_breeze_credentials" in report["blockers"]
    serialized = json.dumps(report)
    assert "secret" not in serialized.lower()
    assert "token" in serialized.lower()  # credential key name only


def test_preflight_blocks_invalid_existing_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _set_ready_environment(monkeypatch)
    (tmp_path / "2026-10-01.json").write_text("{}", encoding="utf-8")

    report = preflight_module.preflight(
        "2026-10-01",
        session_dir=tmp_path,
        now=datetime(2026, 10, 1, 15, 40, tzinfo=IST),
        load_env=False,
    )

    assert report["session_artifact"]["status"] == "invalid"
    assert "existing_session_artifact_invalid" in report["blockers"]
    assert report["safe_to_attempt_collection"] is False
    assert report["ledger_summary"]["invalid_collected_sessions"] == 1


def test_preflight_treats_valid_existing_artifact_as_sealed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _set_ready_environment(monkeypatch)
    path = tmp_path / "2026-10-01.json"
    path.write_text(
        json.dumps(_valid_session_payload("2026-10-01")),
        encoding="utf-8",
    )

    report = preflight_module.preflight(
        "2026-10-01",
        session_dir=tmp_path,
        now=datetime(2026, 10, 1, 15, 40, tzinfo=IST),
        load_env=False,
    )

    assert report["session_artifact"]["status"] == "valid_sealed"
    assert report["collection_already_complete_for_session"] is True
    assert "session_already_sealed" in report["blockers"]
    assert report["safe_to_attempt_collection"] is False
    assert report["ledger_summary"]["valid_collected_sessions"] == 1
    assert len(report["session_artifact"]["sha256"]) == 64


def test_preflight_rejects_nonfrozen_date(tmp_path: Path):
    with pytest.raises(ValueError, match="not a frozen prospective session"):
        preflight_module.preflight(
            "2026-10-02",
            session_dir=tmp_path,
            load_env=False,
        )


def test_preflight_has_no_outcome_or_trading_path():
    guardrails = preflight_module.GUARDRAILS
    assert guardrails["network_request_performed"] is False
    assert guardrails["outcome_scoring_performed"] is False
    assert guardrails["predictor_computed"] is False
    assert guardrails["target_computed"] is False
    assert guardrails["correlation_computed"] is False
    assert guardrails["pnl_scored"] is False
    assert guardrails["model_fitting"] is False
    assert guardrails["implementation_allowed"] is False
    assert guardrails["strategy_d_remains_paused"] is True
