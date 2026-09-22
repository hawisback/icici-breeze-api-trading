from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from services.historical.strategy_a_state_machine_audit import audit_state_machine

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


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


def _insert_session(path: Path, day: date, drift: float) -> None:
    conn = sqlite3.connect(path)
    cursor = datetime.combine(day, time(9, 15), tzinfo=IST)
    last = datetime.combine(day, time(15, 25), tzinfo=IST)
    index = 0
    while cursor <= last:
        start = cursor.astimezone(UTC)
        end = (cursor + timedelta(minutes=5)).astimezone(UTC)
        price = 23000.0 + drift + index * 1.2
        for instrument_id, basis in (
            ("INST-NIFTY-INDEX", price),
            ("INST-NIFTY-FUT-2026-09-29", price + 40.0),
        ):
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
                    basis,
                    basis + 4.0,
                    basis - 2.0,
                    basis + 2.0,
                    100 + index,
                    1000 + index,
                ),
            )
        cursor += timedelta(minutes=5)
        index += 1
    conn.commit()
    conn.close()


def test_state_machine_audit_is_read_only_and_uses_default_window(tmp_path):
    db_path = tmp_path / "historical.db"
    _create_db(db_path)
    for idx, day in enumerate((
        date(2026, 9, 14),
        date(2026, 9, 15),
        date(2026, 9, 16),
        date(2026, 9, 17),
        date(2026, 9, 18),
    )):
        _insert_session(db_path, day, float(idx * 20))

    before = db_path.read_bytes()
    report = audit_state_machine(db_path, sessions=1, source="BREEZE")
    after = db_path.read_bytes()

    assert before == after
    assert report["sessions_found"] == 1
    assert report["thresholds_unchanged"] is True
    assert report["entry_window"]["start"] == "09:45"
    assert report["entry_window"]["end"] == "14:45"
    assert report["entry_window"]["trigger_validity_bars"] == 2
    assert report["entry_window"]["maximum_chase_atr"] == 0.25
    assert report["sessions"][0]["active_futures_contract"] == "INST-NIFTY-FUT-2026-09-29"


def _insert_spot_only_session(path: Path, day: date) -> None:
    conn = sqlite3.connect(path)
    cursor = datetime.combine(day, time(9, 15), tzinfo=IST)
    last = datetime.combine(day, time(15, 25), tzinfo=IST)
    index = 0
    while cursor <= last:
        start = cursor.astimezone(UTC)
        end = (cursor + timedelta(minutes=5)).astimezone(UTC)
        price = 23000.0 + index
        conn.execute(
            """
            INSERT INTO historical_candles (
                instrument_id, interval, start_time, end_time,
                open, high, low, close, volume, open_interest, source
            ) VALUES (?, '5m', ?, ?, ?, ?, ?, ?, ?, ?, 'BREEZE')
            """,
            (
                "INST-NIFTY-INDEX",
                start.isoformat(),
                end.isoformat(),
                price,
                price + 4.0,
                price - 2.0,
                price + 2.0,
                100 + index,
                0,
            ),
        )
        cursor += timedelta(minutes=5)
        index += 1
    conn.commit()
    conn.close()


def test_state_machine_audit_handles_spot_session_without_futures(tmp_path):
    db_path = tmp_path / "historical.db"
    _create_db(db_path)
    _insert_session(db_path, date(2026, 9, 17), 0.0)
    _insert_spot_only_session(db_path, date(2026, 9, 18))

    report = audit_state_machine(db_path, sessions=2, source="BREEZE")

    assert report["audit_type"] == "STRATEGY_A_V3_STATE_MACHINE_READ_ONLY"
    assert report["sessions_found"] == 2
    gap = next(row for row in report["sessions"] if row["date"] == "2026-09-18")
    assert gap["active_futures_contract"] is None
    assert gap["skip_reason"] == "NO_ACTIVE_FUTURES_CONTRACT"
    assert gap["setup_count"] == 0
    assert gap["signal_count"] == 0
    assert gap["final_state"] == "FLAT"
    assert report["aggregate"]["setup_count"] >= 0
    assert report["aggregate"]["signal_count"] >= 0
