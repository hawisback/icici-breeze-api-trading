import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from services.historical.independent_historical_corpus_inventory import (
    GUARDRAILS,
    KNOWN_INSPECTED_WINDOWS,
    analyze_database,
    labels_for_day,
)


def _build_db(path: Path, days: list[str]) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE historical_candles (
                instrument_id TEXT NOT NULL,
                interval TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                open_interest REAL,
                source TEXT NOT NULL,
                PRIMARY KEY (instrument_id, interval, start_time)
            )
            """
        )
        for day_index, day in enumerate(days):
            # 75 bars 09:15 through 15:25 IST. Store UTC ISO strings.
            start_ist = datetime.fromisoformat(f"{day}T09:15:00+05:30")
            expiry = "2026-05-26"
            instrument_id = f"INST-NIFTY-FUT-{expiry}"
            for i in range(75):
                ts = start_ist + timedelta(minutes=5 * i)
                utc = ts.astimezone(timezone.utc).isoformat()
                px = 20000.0 + day_index * 10.0 + i * 0.5
                conn.execute(
                    """
                    INSERT INTO historical_candles (
                        instrument_id, interval, start_time, end_time,
                        open, high, low, close, volume, open_interest, source
                    ) VALUES (?, '5m', ?, ?, ?, ?, ?, ?, ?, ?, 'BREEZE')
                    """,
                    (
                        instrument_id,
                        utc,
                        (ts + timedelta(minutes=5)).astimezone(timezone.utc).isoformat(),
                        px,
                        px + 2.0,
                        px - 2.0,
                        px + 0.5,
                        1000.0,
                        50000.0,
                    ),
                )
        conn.commit()


def test_known_window_registry_is_conservative_and_covers_major_research_ranges():
    labels = {window["label"] for window in KNOWN_INSPECTED_WINDOWS}
    assert "RETROSPECTIVE_MAGNITUDE_ROBUSTNESS" in labels
    assert "DEVELOPMENT_COHORT_4" in labels
    assert "DEVELOPMENT_COHORT_2" in labels
    assert "DEVELOPMENT_COHORT_3" in labels
    assert "DEVELOPMENT_COHORT_1" in labels
    assert "CURRENT_REGIME_MAGNITUDE_PILOT" in labels
    assert {"BLIND05", "BLIND06", "BLIND07", "BLIND08", "BLIND09", "BLIND10", "BLIND11"} <= labels


def test_boundary_labels_and_single_gap_day():
    assert "RETROSPECTIVE_MAGNITUDE_ROBUSTNESS" in labels_for_day("2025-05-14")
    assert "DEVELOPMENT_COHORT_4" in labels_for_day("2025-05-15")
    assert "DEVELOPMENT_COHORT_2" in labels_for_day("2025-09-09")
    assert "DEVELOPMENT_COHORT_3" in labels_for_day("2026-05-05")
    assert "BLIND05" in labels_for_day("2026-05-15")
    assert labels_for_day("2026-05-18") == []
    assert "DEVELOPMENT_COHORT_1" in labels_for_day("2026-05-19")
    assert "CURRENT_REGIME_MAGNITUDE_PILOT" in labels_for_day("2026-09-10")


def test_inventory_finds_only_uncovered_database_sessions_without_outcomes(tmp_path: Path):
    db = tmp_path / "historical.db"
    _build_db(
        db,
        [
            "2025-05-14",
            "2025-05-15",
            "2026-05-15",
            "2026-05-18",
            "2026-05-19",
            "2026-09-10",
        ],
    )

    report = analyze_database(db)

    assert report["summary"] == {
        "database_sessions": 6,
        "known_inspected_database_sessions": 5,
        "not_known_inspected_database_sessions": 1,
        "not_known_inspected_legacy_qa_complete_sessions": 1,
    }
    assert report["not_known_inspected_legacy_qa_complete_dates"] == [
        "2026-05-18"
    ]
    unknown = report["not_known_inspected_sessions"]
    assert len(unknown) == 1
    assert unknown[0]["date"] == "2026-05-18"
    assert unknown[0]["legacy_slice_rows"] == 75
    assert unknown[0]["legacy_75_bar_qa_complete"] is True

    serialized = str(report).lower()
    assert "spearman" not in serialized
    assert "correlation" in serialized  # only guardrail name
    assert "pnl" in serialized  # only guardrail name
    assert "target_computed" in report["guardrails"]
    assert report["guardrails"]["target_computed"] is False
    assert report["guardrails"]["model_fitting"] is False


def test_inventory_does_not_authorize_reuse():
    assert GUARDRAILS["outcome_free_inventory"] is True
    assert GUARDRAILS["unused_session_inventory_does_not_authorize_reuse"] is True
    assert GUARDRAILS["candidate_freeze"] is False
    assert GUARDRAILS["blind_validation_opened"] is False
    assert GUARDRAILS["implementation_allowed"] is False
    assert GUARDRAILS["strategy_d_remains_paused"] is True
