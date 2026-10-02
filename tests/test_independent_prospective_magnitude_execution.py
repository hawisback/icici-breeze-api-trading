from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import json
import pytest

from services.historical.independent_prospective_magnitude_protocol import (
    CONTRACT_BY_DATE,
    EXPECTED_ROWS,
    EXPECTED_SCORABLE_EVENTS,
    SESSION_DATES,
    TERMINAL_OUTCOMES,
)

IST = ZoneInfo("Asia/Kolkata")


def _synthetic_rows(day: str):
    rows = []
    start = datetime.fromisoformat(f"{day}T09:15:00+05:30")
    expiry = CONTRACT_BY_DATE[day]
    session_index = SESSION_DATES.index(day)
    for i in range(77):
        base = 25000.0 + session_index * 20.0 + i * 0.5
        width = 1.0 + (i % 11) * 0.25
        rows.append(
            {
                "timestamp": (start + timedelta(minutes=5 * i)).isoformat(),
                "open": base,
                "high": base + width,
                "low": base - width,
                "close": base + 0.1,
                "volume": 1000.0 + i,
                "open_interest": 100000.0 + session_index * 100 + i,
                "instrument": f"NIFTY FUT {expiry}",
                "futures_expiry": expiry,
                "source": "BREEZE",
            }
        )
    return rows


def _synthetic_market_payload():
    rows = []
    for day in SESSION_DATES:
        rows.extend(_synthetic_rows(day))
    return {
        "protocol_version": "NIFTY_PROSPECTIVE_MAGNITUDE_REPLICATION_V1",
        "provider": "BREEZE",
        "session_dates": list(SESSION_DATES),
        "contract_by_date": dict(CONTRACT_BY_DATE),
        "bars_per_session": 77,
        "rows": EXPECTED_ROWS,
        "quality": {
            "sessions": 30,
            "rows": EXPECTED_ROWS,
            "complete_77_bar_sessions": 30,
            "duplicate_rows": 0,
            "invalid_ohlc_rows": 0,
            "missing_volume_rows": 0,
            "missing_open_interest_rows": 0,
            "nonpositive_volume_rows": 0,
            "nonpositive_open_interest_rows": 0,
            "wrong_contract_rows": 0,
        },
        "futures_rows": rows,
    }


def _synthetic_session_payload(day: str):
    from services.historical.independent_prospective_magnitude_market import (
        SESSION_RESEARCH_TYPE,
    )

    return {
        "research_type": SESSION_RESEARCH_TYPE,
        "protocol_version": "NIFTY_PROSPECTIVE_MAGNITUDE_REPLICATION_V1",
        "research_only": True,
        "prospective_sample": True,
        "qa_only": True,
        "outcome_scoring_performed": False,
        "candidate_frozen": False,
        "blind_validation_opened": False,
        "implementation_allowed": False,
        "provider": "BREEZE",
        "session_date": day,
        "futures_expiry": CONTRACT_BY_DATE[day],
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
            "futures_expiry": CONTRACT_BY_DATE[day],
            "status": 200,
            "error": None,
            "raw_count": 77,
            "accepted_count": 77,
        },
        "futures_rows": _synthetic_rows(day),
    }


def test_each_session_is_collectible_only_after_its_own_close():
    from services.historical.independent_prospective_magnitude_market import (
        assert_session_collection_open,
    )

    day = "2026-10-01"
    before = datetime(2026, 10, 1, 15, 39, tzinfo=IST)
    boundary = datetime(2026, 10, 1, 15, 40, tzinfo=IST)

    with pytest.raises(RuntimeError, match="collection is locked"):
        assert_session_collection_open(day, before)
    assert_session_collection_open(day, boundary)

    with pytest.raises(ValueError, match="not a frozen prospective session"):
        assert_session_collection_open("2026-10-02", boundary)


def test_analyzer_remains_locked_until_entire_forward_window_completes():
    from services.historical.independent_prospective_magnitude_findings import (
        assert_scoring_window_open,
    )

    before = datetime(2026, 11, 16, 15, 39, tzinfo=IST)
    boundary = datetime(2026, 11, 16, 15, 40, tzinfo=IST)

    with pytest.raises(RuntimeError, match="scoring is locked"):
        assert_scoring_window_open(before)
    assert_scoring_window_open(boundary)


def test_sealed_session_artifacts_are_qa_only_and_assemble_exactly(tmp_path: Path):
    from services.historical.independent_prospective_magnitude_market import (
        assemble_session_artifacts,
    )

    for day in SESSION_DATES:
        (tmp_path / f"{day}.json").write_text(
            json.dumps(_synthetic_session_payload(day)),
            encoding="utf-8",
        )

    report = assemble_session_artifacts(tmp_path)
    assert report["rows"] == EXPECTED_ROWS == 2310
    assert report["qa_only_assembly"] is True
    assert report["outcome_scoring_performed"] is False
    assert report["session_dates"] == SESSION_DATES
    assert report["contract_by_date"] == CONTRACT_BY_DATE
    assert report["quality"]["complete_77_bar_sessions"] == 30


def test_assembler_rejects_missing_or_scored_session_artifact(tmp_path: Path):
    from services.historical.independent_prospective_magnitude_market import (
        assemble_session_artifacts,
    )

    for day in SESSION_DATES:
        payload = _synthetic_session_payload(day)
        (tmp_path / f"{day}.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )

    (tmp_path / f"{SESSION_DATES[-1]}.json").unlink()
    with pytest.raises(ValueError, match="missing sealed session artifact"):
        assemble_session_artifacts(tmp_path)

    day = SESSION_DATES[-1]
    payload = _synthetic_session_payload(day)
    payload["outcome_scoring_performed"] = True
    (tmp_path / f"{day}.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="outcome scoring must not be performed"):
        assemble_session_artifacts(tmp_path)


def test_market_validation_enforces_exact_roll_and_shape():
    from services.historical.independent_prospective_magnitude_findings import (
        _validate,
    )

    payload = _synthetic_market_payload()
    frame = _validate(payload)
    assert len(frame) == EXPECTED_ROWS
    assert frame.loc[frame["date"] == "2026-10-27", "futures_expiry"].eq(
        "2026-10-27"
    ).all()
    assert frame.loc[frame["date"] == "2026-10-28", "futures_expiry"].eq(
        "2026-11-23"
    ).all()

    bad = _synthetic_market_payload()
    bad["futures_rows"][17 * 77]["futures_expiry"] = "2026-10-27"
    with pytest.raises(ValueError, match="wrong futures expiry"):
        _validate(bad)


def test_exact_30_session_sample_yields_1980_frozen_events():
    from services.historical.independent_prospective_magnitude_findings import (
        _build_events,
        _validate,
    )

    frame = _validate(_synthetic_market_payload())
    events = _build_events(frame)
    assert len(events) == EXPECTED_SCORABLE_EVENTS == 1980
    assert events.groupby("date").size().eq(66).all()
    assert events.groupby("block")["date"].nunique().to_dict() == {
        "block1": 10,
        "block2": 10,
        "block3": 10,
    }


def test_replication_gate_is_exactly_predeclared():
    from services.historical.independent_prospective_magnitude_findings import (
        _evaluate_gate,
    )

    assert _evaluate_gate(
        scorable_events=1980,
        pooled_spearman=0.01,
        block_spearman={"block1": 0.01, "block2": 0.02, "block3": 0.03},
        bootstrap_low=0.001,
    ) == []

    assert _evaluate_gate(
        scorable_events=1979,
        pooled_spearman=0.0,
        block_spearman={"block1": 0.01, "block2": -0.01, "block3": 0.03},
        bootstrap_low=0.0,
    ) == [
        "minimum_scorable_events",
        "pooled_spearman_not_positive",
        "block_spearman_not_positive_in_all_three_blocks",
        "bootstrap_lower_bound_not_positive",
    ]


def test_analyzer_produces_no_quartile_threshold_or_trading_promotion(monkeypatch):
    import services.historical.independent_prospective_magnitude_findings as findings

    monkeypatch.setattr(findings, "_spearman", lambda x, y: 0.2)
    monkeypatch.setattr(findings, "_bootstrap", lambda events: (0.1, 0.3))

    report = findings.analyze(
        _synthetic_market_payload(),
        source_sha256="abc123",
        now=datetime(2026, 11, 16, 15, 40, tzinfo=IST),
    )

    assert report["replication_gate"] == {"passed": True, "failures": []}
    assert report["results"] == {
        "pooled_spearman": 0.2,
        "block_spearman": {
            "block1": 0.2,
            "block2": 0.2,
            "block3": 0.2,
        },
        "session_cluster_bootstrap_95pct": [0.1, 0.3],
    }
    assert "quartile" not in str(report["results"]).lower()
    assert report["terminal_outcome"] == TERMINAL_OUTCOMES["if_pass"]
    assert report["guardrails"]["quartile_analysis_produced"] is False
    assert report["guardrails"]["candidate_frozen"] is False
    assert report["guardrails"]["blind_validation_allowed"] is False
    assert report["guardrails"]["implementation_allowed"] is False


def test_sealed_writer_allows_identical_rerun_but_refuses_changed_bytes(tmp_path: Path):
    from services.historical.independent_prospective_magnitude_market import (
        _write_report,
    )

    output = tmp_path / "sealed.json"
    report = _synthetic_session_payload(SESSION_DATES[0])

    _write_report(report, output)
    first = output.read_bytes()

    _write_report(report, output)
    assert output.read_bytes() == first

    changed = _synthetic_session_payload(SESSION_DATES[0])
    changed["request_diagnostics"]["raw_count"] = 78
    with pytest.raises(FileExistsError, match="refusing to overwrite sealed research artifact"):
        _write_report(changed, output)

    assert output.read_bytes() == first
