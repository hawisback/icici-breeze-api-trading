from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from services.historical.strategy_a_data_audit import audit_database

IST = ZoneInfo("Asia/Kolkata")


def _create_db(path: Path) -> None:
    conn = sqlite3.connect(path)
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
    conn.commit()
    conn.close()


def _insert_session(path: Path, day: date, *, omit_future_minute: int | None = None) -> None:
    conn = sqlite3.connect(path)
    cursor = datetime.combine(day, time(9, 15), tzinfo=IST)
    last = datetime.combine(day, time(15, 25), tzinfo=IST)
    index = 0
    while cursor <= last:
        for instrument_id, base in (
            ("INST-NIFTY-INDEX", 23000.0),
            ("INST-NIFTY-FUT-2026-09-29", 23100.0),
        ):
            if (
                instrument_id.startswith("INST-NIFTY-FUT-")
                and omit_future_minute is not None
                and cursor.hour == 10
                and cursor.minute == omit_future_minute
            ):
                continue
            start = cursor.astimezone(ZoneInfo("UTC"))
            end = (cursor + timedelta(minutes=5)).astimezone(ZoneInfo("UTC"))
            price = base + index
            conn.execute(
                """
                INSERT INTO historical_candles (
                    instrument_id, interval, start_time, end_time,
                    open, high, low, close, volume, open_interest, source
                ) VALUES (?, '5m', ?, ?, ?, ?, ?, ?, ?, ?, 'BREEZE')
                """,
                (
                    instrument_id,
                    start.isoformat(),
                    end.isoformat(),
                    price,
                    price + 2,
                    price - 2,
                    price + 1,
                    100,
                    1000,
                ),
            )
        cursor += timedelta(minutes=5)
        index += 1
    conn.commit()
    conn.close()


def test_strategy_a_data_audit_passes_complete_recent_session(tmp_path):
    db_path = tmp_path / "historical.db"
    _create_db(db_path)
    _insert_session(db_path, date(2026, 9, 15))
    _insert_session(db_path, date(2026, 9, 16))
    _insert_session(db_path, date(2026, 9, 17))

    report = audit_database(db_path, sessions=1, source="BREEZE")

    assert report["sessions_found"] == 1
    assert report["all_sessions_data_ready"] is True
    assert report["refetch_recommended"] is False
    session = report["sessions"][0]
    assert session["date"] == "2026-09-17"
    assert session["active_futures_contract"] == "INST-NIFTY-FUT-2026-09-29"
    assert session["futures"]["rows"] == 75
    assert session["strategy_a_input_readiness"]["entry_window_coverage_pct"] == 100.0
    assert session["strategy_a_input_readiness"]["warmup_15m_bars_at_first_decision"] >= 50


def test_strategy_a_data_audit_recommends_targeted_refetch_for_entry_gap(tmp_path):
    db_path = tmp_path / "historical.db"
    _create_db(db_path)
    _insert_session(db_path, date(2026, 9, 15))
    _insert_session(db_path, date(2026, 9, 16))
    _insert_session(db_path, date(2026, 9, 17), omit_future_minute=0)

    report = audit_database(db_path, sessions=1, source="BREEZE")

    assert report["all_sessions_data_ready"] is False
    assert report["refetch_recommended"] is True
    assert report["refetch_dates"] == ["2026-09-17"]
    session = report["sessions"][0]
    assert "FUTURES_5M_GAPS" in session["reasons"]
    assert "STRATEGY_A_15M_ENTRY_WINDOW_GAPS" in session["reasons"]
    assert session["strategy_a_input_readiness"]["entry_window_coverage_pct"] < 100.0


def test_strategy_a_data_audit_recommends_refetch_when_warmup_is_insufficient(tmp_path):
    db_path = tmp_path / "historical.db"
    _create_db(db_path)
    _insert_session(db_path, date(2026, 9, 17))

    report = audit_database(db_path, sessions=1, source="BREEZE")

    session = report["sessions"][0]
    assert "EMA50_WARMUP_INSUFFICIENT" in session["reasons"]
    assert report["refetch_recommended"] is True
    assert report["refetch_dates"] == ["2026-09-17"]
