import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import services.historical.independent_market_structure_atlas as atlas
from services.historical.independent_market_structure_atlas_protocol import (
    EXCLUDED_ALREADY_STUDIED_HYPOTHESES,
    GUARDRAILS,
    PATTERN_FAMILIES,
    PROTOCOL_VERSION,
)


def _weekdays(start: date, count: int) -> list[date]:
    out: list[date] = []
    cursor = start
    while len(out) < count:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


def _build_db(path: Path, session_count: int = 24, add_1530: bool = True) -> None:
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
        days = _weekdays(date(2025, 1, 2), session_count)
        previous_close = 20000.0

        for session_index, day in enumerate(days):
            dte_cycle = [0, 3, 8, 15]
            dte = dte_cycle[session_index % len(dte_cycle)]
            expiry = day + timedelta(days=dte)
            instrument = f"INST-NIFTY-FUT-{expiry.isoformat()}"

            # Smoothly varying session scale creates persistent daily ranges.
            scale = 1.0 + session_index * 0.05
            gap = scale * 2.0
            session_open = previous_close + gap
            start_ist = datetime.fromisoformat(
                f"{day.isoformat()}T09:15:00+05:30"
            )
            close = session_open

            for i in range(75 + (1 if add_1530 else 0)):
                ts = start_ist + timedelta(minutes=5 * i)
                if i < 12:
                    step = 1.2 * scale
                elif i < 63:
                    step = 0.35 * scale
                else:
                    step = 0.9 * scale
                open_px = close
                close = open_px + step
                width = (0.8 + 0.03 * i) * scale
                high = max(open_px, close) + width
                low = min(open_px, close) - width
                conn.execute(
                    """
                    INSERT INTO historical_candles (
                        instrument_id, interval, start_time, end_time,
                        open, high, low, close, volume, open_interest, source
                    ) VALUES (?, '5m', ?, ?, ?, ?, ?, ?, ?, ?, 'BREEZE')
                    """,
                    (
                        instrument,
                        ts.astimezone(timezone.utc).isoformat(),
                        (ts + timedelta(minutes=5)).astimezone(
                            timezone.utc
                        ).isoformat(),
                        open_px,
                        high,
                        low,
                        close,
                        1000 + i,
                        50000 + session_index * 100 + i,
                    ),
                )
            previous_close = close

        conn.commit()


def test_protocol_is_exploratory_and_excludes_known_magnitude_hypothesis():
    assert PROTOCOL_VERSION == "NIFTY_BREEZE_MARKET_STRUCTURE_ATLAS_V1"
    assert (
        "trailing_30m_range_bps_vs_next_30m_max_absolute_excursion_bps"
        in EXCLUDED_ALREADY_STUDIED_HYPOTHESES
    )
    assert len(PATTERN_FAMILIES) == 6
    assert GUARDRAILS["exploratory"] is True
    assert GUARDRAILS["blind_validation"] is False
    assert GUARDRAILS["candidate_freeze"] is False
    assert GUARDRAILS["implementation_allowed"] is False
    assert GUARDRAILS["pnl_scored"] is False
    assert GUARDRAILS["known_magnitude_thesis_retested"] is False


def test_common_slice_ignores_optional_1530_bar(tmp_path: Path):
    db = tmp_path / "historical.db"
    _build_db(db, session_count=6, add_1530=True)

    raw = atlas._read_breeze_rows(db)

    assert raw["date"].nunique() == 6
    assert raw.groupby("date").size().eq(75).all()
    assert raw["timestamp"].dt.strftime("%H:%M").max() == "15:25"


def test_atlas_prepares_exact_complete_sessions_and_six_blocks(tmp_path: Path):
    db = tmp_path / "historical.db"
    _build_db(db, session_count=24, add_1530=True)

    raw = atlas._read_breeze_rows(db)
    bars, sessions, rejected = atlas._validate_and_prepare_sessions(raw)
    blocks = atlas._chronological_blocks(sessions["date"].tolist(), 6)

    assert len(sessions) == 24
    assert len(bars) == 24 * 75
    assert rejected == []
    assert [len(blocks[f"block{i}"]) for i in range(1, 7)] == [4] * 6
    assert sessions["dte_calendar_days"].isin([0, 3, 8, 15]).all()


def test_full_atlas_runs_on_synthetic_breeze_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    db = tmp_path / "historical.db"
    _build_db(db, session_count=48, add_1530=True)
    monkeypatch.setitem(atlas.BOOTSTRAP, "draws", 200)

    report = atlas.analyze_database(db)

    assert report["protocol_version"] == PROTOCOL_VERSION
    assert report["exploratory"] is True
    assert report["qa"]["accepted_complete_sessions"] == 48
    assert report["qa"]["rejected_sessions"] == 0
    assert set(report["results"]) == {
        "intraday_volatility_seasonality",
        "overnight_gap_vs_session_range",
        "opening_range_vs_remaining_range",
        "daily_range_persistence",
        "weekday_range_seasonality",
        "expiry_distance_range_structure",
    }

    for name in (
        "overnight_gap_vs_session_range",
        "opening_range_vs_remaining_range",
        "daily_range_persistence",
    ):
        result = report["results"][name]
        assert -1.0 <= result["pooled_spearman"] <= 1.0
        assert len(result["block_spearman"]) == 6
        assert len(result["bootstrap_95pct"]) == 2

    intraday = report["results"]["intraday_volatility_seasonality"]
    assert len(intraday["slot_profile"]) == 74
    assert intraday["opening_to_midday_ratio"] > 1.0
    assert intraday["late_to_midday_ratio"] > 1.0

    serialized = str(report).lower()
    assert "next_30m_max_absolute_excursion_bps" in serialized  # exclusion list only
    assert "pnl_scored" in serialized  # guardrail only
    assert report["guardrails"]["pnl_scored"] is False
    assert report["guardrails"]["implementation_allowed"] is False


def test_scalar_pattern_label_requires_effect_stability_and_ci(monkeypatch):
    sessions = atlas.pd.DataFrame(
        {
            "date": [f"2025-01-{i:02d}" for i in range(1, 19)],
            "x": list(range(18)),
            "y": [value * 2.0 for value in range(18)],
        }
    )
    blocks = atlas._chronological_blocks(sessions["date"].tolist(), 6)
    monkeypatch.setitem(atlas.BOOTSTRAP, "draws", 100)

    result = atlas._scalar_relationship(sessions, blocks, "x", "y")

    assert result["pooled_spearman"] > 0.99
    assert result["same_sign_blocks"] == 6
    assert result["bootstrap_95pct"][0] > 0.0
    assert result["exploratory_pattern_label"] is True
