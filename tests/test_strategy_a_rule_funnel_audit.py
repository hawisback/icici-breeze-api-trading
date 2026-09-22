from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from services.historical.strategy_a_rule_funnel_audit import audit_rule_funnel

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


def _insert_session(path: Path, day: date, offset: float) -> None:
    conn = sqlite3.connect(path)
    cursor = datetime.combine(day, time(9, 15), tzinfo=IST)
    last = datetime.combine(day, time(15, 25), tzinfo=IST)
    index = 0
    while cursor <= last:
        start = cursor.astimezone(UTC)
        end = (cursor + timedelta(minutes=5)).astimezone(UTC)
        trend = offset + index * 1.5
        for instrument_id, base in (
            ("INST-NIFTY-INDEX", 23000.0),
            ("INST-NIFTY-FUT-2026-09-29", 23100.0),
        ):
            price = base + trend
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
                    price + 4.0,
                    price - 3.0,
                    price + 2.0,
                    100 + index,
                    1000 + index,
                ),
            )
        cursor += timedelta(minutes=5)
        index += 1
    conn.commit()
    conn.close()


def test_rule_funnel_audit_is_directional_and_read_only(tmp_path):
    db_path = tmp_path / "historical.db"
    _create_db(db_path)
    for idx, day in enumerate((
        date(2026, 9, 14),
        date(2026, 9, 15),
        date(2026, 9, 16),
        date(2026, 9, 17),
        date(2026, 9, 18),
    )):
        _insert_session(db_path, day, float(idx * 100))

    before = db_path.read_bytes()
    report = audit_rule_funnel(db_path, sessions=1, source="BREEZE")
    after = db_path.read_bytes()

    assert before == after
    assert report["sessions_found"] == 1
    assert report["completed_15m_decision_bars"] == 21
    assert report["directional_evaluations"] == 42
    assert report["aggregate"]["data_quality_counts"] == {}
    assert report["thresholds"]["momentum_adx_min_delta_2bars"] == -2.0
    assert report["thresholds"]["momentum_ema20_slope_min_atr"] == 0.0
    assert report["thresholds"]["momentum_ema20_slope_max_atr"] == 0.15
    assert "trend" in report["aggregate"]["gate_funnel"]
    assert "confirmation" in report["aggregate"]["gate_funnel"]
    assert "confluence" in report["aggregate"]["gate_funnel"]
