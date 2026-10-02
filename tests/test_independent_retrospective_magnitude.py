from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import sqlite3

import pytest

from services.historical.independent_current_regime_magnitude_protocol import (
    BOOTSTRAP as CURRENT_BOOTSTRAP,
    PREDICTOR as CURRENT_PREDICTOR,
    TARGET as CURRENT_TARGET,
)
from services.historical.independent_retrospective_magnitude_protocol import (
    BOOTSTRAP,
    CORPUS_ROLE,
    EXPECTED_SCORABLE_EVENTS_PER_SESSION,
    FUTURES_CONTRACT_PERIODS,
    GUARDRAILS,
    PREDICTOR,
    QA_POLICY,
    REPLICATION_GATE,
    SESSION_LAST_BAR,
    SESSION_START,
    TARGET,
    WINDOW_END,
    WINDOW_START,
)

IST = ZoneInfo("Asia/Kolkata")


def _period(day: str):
    for item in FUTURES_CONTRACT_PERIODS:
        if item["first_date"] <= day <= item["last_date"]:
            return item
    raise AssertionError(day)


def _session_days(n: int) -> list[str]:
    cursor = date.fromisoformat(WINDOW_START)
    end = date.fromisoformat(WINDOW_END)
    result = []
    while cursor <= end and len(result) < n:
        if cursor.weekday() < 5:
            result.append(cursor.isoformat())
        cursor += timedelta(days=1)
    assert len(result) == n
    return result


def _make_db(
    path: Path,
    days: list[str],
    *,
    include_1530: bool = False,
    source: str = "BREEZE",
    zero_volume_oi: bool = False,
) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE historical_candles (
                instrument_id TEXT NOT NULL,
                interval TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume INTEGER NOT NULL,
                open_interest INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'BREEZE',
                PRIMARY KEY (instrument_id, interval, start_time)
            )
            """
        )
        for session_index, day in enumerate(days):
            period = _period(day)
            start = datetime.fromisoformat(f"{day}T09:15:00+05:30")
            count = 76 if include_1530 else 75
            for i in range(count):
                local_ts = start + timedelta(minutes=5 * i)
                utc_ts = local_ts.astimezone(ZoneInfo("UTC"))
                base = 22000.0 + session_index * 3.0 + i * 0.5
                width = 2.0 + (i % 9) * 0.2
                conn.execute(
                    """
                    INSERT INTO historical_candles (
                        instrument_id, interval, start_time, end_time,
                        open, high, low, close, volume, open_interest, source
                    ) VALUES (?, '5m', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        period["instrument_id"],
                        utc_ts.isoformat(),
                        (utc_ts + timedelta(minutes=5)).isoformat(),
                        base,
                        base + width,
                        base - width,
                        base + 0.1,
                        0 if zero_volume_oi else 1000 + i,
                        0 if zero_volume_oi else 100000 + i,
                        source,
                    ),
                )
        conn.commit()


def test_retrospective_window_is_pre_cohort4_and_not_validation():
    assert WINDOW_START == "2025-01-01"
    assert WINDOW_END == "2025-05-14"
    assert CORPUS_ROLE == "RETROSPECTIVE_ROBUSTNESS_NOT_VALIDATION"
    assert FUTURES_CONTRACT_PERIODS[0]["expiry"] == "2025-01-30"
    assert FUTURES_CONTRACT_PERIODS[-1]["expiry"] == "2025-05-29"
    assert SESSION_START == "09:15"
    assert SESSION_LAST_BAR == "15:25"
    assert QA_POLICY["minimum_complete_sessions"] == 60
    assert REPLICATION_GATE["minimum_scorable_events"] == 60 * 64


def test_predictor_target_and_bootstrap_are_exactly_carried_forward():
    assert PREDICTOR == CURRENT_PREDICTOR
    assert TARGET == CURRENT_TARGET
    assert BOOTSTRAP == CURRENT_BOOTSTRAP
    assert EXPECTED_SCORABLE_EVENTS_PER_SESSION == 64


def test_legacy_optional_1530_bar_is_ignored(tmp_path: Path):
    from services.historical.independent_retrospective_magnitude_findings import (
        _qa_sessions,
        _read_breeze_rows,
    )

    db = tmp_path / "historical.db"
    day = _session_days(1)
    _make_db(db, day, include_1530=True)

    frame = _read_breeze_rows(db)
    assert len(frame) == 75
    accepted, rejected, _ = _qa_sessions(frame)
    assert list(accepted) == day
    assert rejected == []


def test_volume_and_oi_do_not_drive_session_inclusion(tmp_path: Path):
    from services.historical.independent_retrospective_magnitude_findings import (
        _qa_sessions,
        _read_breeze_rows,
    )

    db = tmp_path / "historical.db"
    day = _session_days(1)
    _make_db(db, day, zero_volume_oi=True)

    accepted, rejected, _ = _qa_sessions(_read_breeze_rows(db))
    assert list(accepted) == day
    assert rejected == []


def test_75_bar_session_builds_64_exact_events(tmp_path: Path):
    from services.historical.independent_retrospective_magnitude_findings import (
        _build_events,
        _chronological_blocks,
        _qa_sessions,
        _read_breeze_rows,
    )

    db = tmp_path / "historical.db"
    days = _session_days(3)
    _make_db(db, days)
    accepted, rejected, _ = _qa_sessions(_read_breeze_rows(db))
    assert rejected == []

    blocks = _chronological_blocks(sorted(accepted))
    events = _build_events(accepted, blocks)
    assert len(events) == 3 * 64
    assert events.groupby("date").size().eq(64).all()
    assert [len(blocks[f"block{i}"]) for i in range(1, 4)] == [1, 1, 1]


def test_chronological_thirds_receive_remainder_earliest():
    from services.historical.independent_retrospective_magnitude_findings import (
        _chronological_blocks,
    )

    days = [f"d{i:02d}" for i in range(61)]
    blocks = _chronological_blocks(days)
    assert [len(blocks[f"block{i}"]) for i in range(1, 4)] == [21, 20, 20]
    assert blocks["block1"] + blocks["block2"] + blocks["block3"] == days


def test_full_60_session_synthetic_database_can_pass_frozen_gate(
    tmp_path: Path, monkeypatch
):
    import services.historical.independent_retrospective_magnitude_findings as findings

    db = tmp_path / "historical.db"
    days = _session_days(60)
    _make_db(db, days, include_1530=True)

    monkeypatch.setattr(findings, "_spearman", lambda x, y: 0.2)
    monkeypatch.setattr(
        findings,
        "_bootstrap",
        lambda events, session_dates: (0.1, 0.3),
    )

    report = findings.analyze_database(db)
    assert report["qa"]["passed"] is True
    assert report["qa"]["complete_sessions"] == 60
    assert report["scorable_events"] == 60 * 64 == 3840
    assert report["results"] == {
        "pooled_spearman": 0.2,
        "block_spearman": {
            "block1": 0.2,
            "block2": 0.2,
            "block3": 0.2,
        },
        "session_cluster_bootstrap_95pct": [0.1, 0.3],
    }
    assert report["replication_gate"] == {"passed": True, "failures": []}
    assert report["decision"] == "RETROSPECTIVE_MAGNITUDE_RELATIONSHIP_ROBUST"
    assert "quartile" not in str(report["results"]).lower()
    assert report["prospective_validation"] is False
    assert report["guardrails"] == GUARDRAILS
    assert report["guardrails"]["implementation_allowed"] is False
    assert report["guardrails"]["strategy_d_remains_paused"] is True


def test_non_breeze_rows_are_not_used(tmp_path: Path):
    from services.historical.independent_retrospective_magnitude_findings import (
        _read_breeze_rows,
    )

    db = tmp_path / "historical.db"
    _make_db(db, _session_days(1), source="OTHER")
    with pytest.raises(ValueError, match="no frozen-window Breeze"):
        _read_breeze_rows(db)
