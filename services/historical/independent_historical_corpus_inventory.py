"""Outcome-free inventory of historical Breeze NIFTY futures research usage.

This module answers a provenance question only: which historical Breeze
sessions are already covered by an inspected research window, and which
database sessions are not known to have been inspected?

It deliberately does not compute predictors, targets, correlations, P&L,
thresholds, or strategy outcomes. Its purpose is to prevent accidental reuse
of statistically contaminated historical data.

Known research coverage is conservative: if a database session falls inside a
previously inspected/frozen date window, it is treated as already consumed for
future independent model scoring even when windows overlap.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

RESEARCH_TYPE = "NIFTY_HISTORICAL_CORPUS_INVENTORY_V1"
PROTOCOL_VERSION = "NIFTY_HISTORICAL_CORPUS_INVENTORY_V1"

WINDOW_START = "2025-01-01"
WINDOW_END = "2026-09-18"
LEGACY_SESSION_START = "09:15"
LEGACY_SESSION_LAST_BAR = "15:25"
LEGACY_EXPECTED_BARS = 75

# Conservative window registry derived from frozen/recorded project protocols.
# Overlap is intentional and retained in the output.
KNOWN_INSPECTED_WINDOWS = [
    {
        "label": "RETROSPECTIVE_MAGNITUDE_ROBUSTNESS",
        "first_date": "2025-01-01",
        "last_date": "2025-05-14",
        "role": "RETROSPECTIVE_ROBUSTNESS_INSPECTED",
        "source_protocol": "NIFTY_RETROSPECTIVE_MAGNITUDE_ROBUSTNESS_V1",
    },
    {
        "label": "DEVELOPMENT_COHORT_4",
        "first_date": "2025-05-15",
        "last_date": "2025-09-08",
        "role": "CLEAN_UNUSED_BUT_INSPECTED_DEVELOPMENT_EVIDENCE",
        "source_protocol": "DEVELOPMENT_COHORT_4_V1",
    },
    {
        "label": "DEVELOPMENT_COHORT_2",
        "first_date": "2025-09-09",
        "last_date": "2025-12-23",
        "role": "INSPECTED_DEVELOPMENT",
        "source_protocol": "DEVELOPMENT_COHORT_2_V1_1",
    },
    {
        "label": "BLIND11",
        "first_date": "2025-12-24",
        "last_date": "2026-01-07",
        "role": "PREVIOUSLY_INSPECTED",
        "source_protocol": "PREVIOUSLY_INSPECTED_WINDOWS_REGISTRY",
    },
    {
        "label": "BLIND10",
        "first_date": "2026-01-08",
        "last_date": "2026-01-22",
        "role": "PREVIOUSLY_INSPECTED",
        "source_protocol": "PREVIOUSLY_INSPECTED_WINDOWS_REGISTRY",
    },
    {
        "label": "BLIND09",
        "first_date": "2026-01-23",
        "last_date": "2026-02-06",
        "role": "PREVIOUSLY_INSPECTED",
        "source_protocol": "PREVIOUSLY_INSPECTED_WINDOWS_REGISTRY",
    },
    {
        "label": "BLIND08",
        "first_date": "2026-02-09",
        "last_date": "2026-02-20",
        "role": "PREVIOUSLY_INSPECTED",
        "source_protocol": "PREVIOUSLY_INSPECTED_WINDOWS_REGISTRY",
    },
    {
        "label": "DEVELOPMENT_COHORT_3",
        "first_date": "2026-01-02",
        "last_date": "2026-05-05",
        "role": "INSPECTED_DEVELOPMENT",
        "source_protocol": "DEVELOPMENT_COHORT_3_V1",
    },
    {
        "label": "BLIND07",
        "first_date": "2026-04-01",
        "last_date": "2026-04-16",
        "role": "PREVIOUSLY_INSPECTED",
        "source_protocol": "PREVIOUSLY_INSPECTED_WINDOWS_REGISTRY",
    },
    {
        "label": "BLIND06",
        "first_date": "2026-04-17",
        "last_date": "2026-04-30",
        "role": "PREVIOUSLY_INSPECTED",
        "source_protocol": "PREVIOUSLY_INSPECTED_WINDOWS_REGISTRY",
    },
    {
        "label": "BLIND05",
        "first_date": "2026-05-04",
        "last_date": "2026-05-15",
        "role": "PREVIOUSLY_INSPECTED",
        "source_protocol": "PREVIOUSLY_INSPECTED_WINDOWS_REGISTRY",
    },
    {
        "label": "DEVELOPMENT_COHORT_1",
        "first_date": "2026-05-19",
        "last_date": "2026-09-09",
        "role": "INSPECTED_DEVELOPMENT",
        "source_protocol": "SHORT_SWING_DEVELOPMENT_V2",
    },
    {
        "label": "CURRENT_REGIME_MAGNITUDE_PILOT",
        "first_date": "2026-09-10",
        "last_date": "2026-09-18",
        "role": "INSPECTED_MAGNITUDE_PILOT_DB_OVERLAP",
        "source_protocol": "NIFTY_CURRENT_REGIME_MAGNITUDE_REPLICATION_V1",
    },
]

GUARDRAILS = {
    "research_only": True,
    "outcome_free_inventory": True,
    "predictor_computed": False,
    "target_computed": False,
    "correlation_computed": False,
    "pnl_scored": False,
    "threshold_selection": False,
    "model_fitting": False,
    "candidate_freeze": False,
    "blind_validation_opened": False,
    "implementation_allowed": False,
    "unused_session_inventory_does_not_authorize_reuse": True,
    "strategy_d_remains_paused": True,
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def labels_for_day(day: str) -> list[str]:
    return [
        str(window["label"])
        for window in KNOWN_INSPECTED_WINDOWS
        if str(window["first_date"]) <= day <= str(window["last_date"])
    ]


def _read_rows(db_path: Path) -> pd.DataFrame:
    if not db_path.exists():
        raise FileNotFoundError(f"historical database not found: {db_path}")
    with sqlite3.connect(str(db_path)) as conn:
        table = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='historical_candles'"
        ).fetchone()
        if not table:
            raise ValueError("historical_candles table not found")
        frame = pd.read_sql_query(
            """
            SELECT instrument_id, interval, start_time, open, high, low, close,
                   volume, open_interest, source
            FROM historical_candles
            WHERE interval = '5m'
              AND source = 'BREEZE'
              AND instrument_id LIKE 'INST-NIFTY-FUT-%'
            ORDER BY start_time ASC
            """,
            conn,
        )
    if frame.empty:
        raise ValueError("no Breeze NIFTY futures rows found")
    frame["timestamp"] = pd.to_datetime(
        frame["start_time"], utc=True, errors="raise"
    ).dt.tz_convert("Asia/Kolkata")
    frame["date"] = frame["timestamp"].dt.date.astype(str)
    return frame.loc[
        (frame["date"] >= WINDOW_START) & (frame["date"] <= WINDOW_END)
    ].copy()


def _legacy_qa(group: pd.DataFrame) -> dict[str, Any]:
    sliced = group.loc[
        group["timestamp"].dt.time.between(
            time.fromisoformat(LEGACY_SESSION_START),
            time.fromisoformat(LEGACY_SESSION_LAST_BAR),
            inclusive="both",
        )
    ].sort_values("timestamp").copy()

    for column in ("open", "high", "low", "close"):
        sliced[column] = pd.to_numeric(sliced[column], errors="coerce")
    numeric = sliced[["open", "high", "low", "close"]].to_numpy(dtype=float)

    reasons: list[str] = []
    if len(sliced) != LEGACY_EXPECTED_BARS:
        reasons.append(f"legacy_bar_count_{len(sliced)}")
    if sliced["timestamp"].duplicated().any():
        reasons.append("duplicate_timestamp")
    if len(sliced) and not np.isfinite(numeric).all():
        reasons.append("non_finite_ohlc")
    elif len(sliced) and not (numeric > 0.0).all():
        reasons.append("nonpositive_price")
    elif len(sliced) and not (
        (sliced["low"] <= sliced["open"])
        & (sliced["open"] <= sliced["high"])
        & (sliced["low"] <= sliced["close"])
        & (sliced["close"] <= sliced["high"])
    ).all():
        reasons.append("invalid_ohlc")

    return {
        "legacy_slice_rows": int(len(sliced)),
        "legacy_75_bar_qa_complete": not reasons,
        "legacy_qa_reasons": reasons,
        "instrument_ids": sorted(
            {str(value) for value in group["instrument_id"].dropna().unique()}
        ),
        "raw_5m_rows": int(len(group)),
        "volume_rows_present": int(group["volume"].notna().sum()),
        "open_interest_rows_present": int(group["open_interest"].notna().sum()),
    }


def analyze_database(db_path: Path) -> dict[str, Any]:
    frame = _read_rows(db_path)
    sessions: list[dict[str, Any]] = []
    for day, group in frame.groupby("date", sort=True):
        day = str(day)
        labels = labels_for_day(day)
        qa = _legacy_qa(group)
        sessions.append(
            {
                "date": day,
                "known_inspected_labels": labels,
                "known_inspected": bool(labels),
                "not_known_inspected": not labels,
                **qa,
            }
        )

    unused = [row for row in sessions if row["not_known_inspected"]]
    unused_complete = [
        row for row in unused if row["legacy_75_bar_qa_complete"]
    ]
    consumed = [row for row in sessions if row["known_inspected"]]

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "research_only": True,
        "source_database": str(db_path),
        "source_database_sha256": _sha256(db_path),
        "inventory_window": [WINDOW_START, WINDOW_END],
        "known_inspected_windows": KNOWN_INSPECTED_WINDOWS,
        "summary": {
            "database_sessions": len(sessions),
            "known_inspected_database_sessions": len(consumed),
            "not_known_inspected_database_sessions": len(unused),
            "not_known_inspected_legacy_qa_complete_sessions": len(
                unused_complete
            ),
        },
        "not_known_inspected_sessions": unused,
        "not_known_inspected_legacy_qa_complete_dates": [
            row["date"] for row in unused_complete
        ],
        "all_database_sessions": sessions,
        "interpretation": (
            "This is an outcome-free provenance inventory only. A session being "
            "listed as not known inspected does not by itself make it suitable "
            "for future model development or validation."
        ),
        "guardrails": GUARDRAILS,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inventory historical Breeze NIFTY research usage"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/market/historical.db"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/independent_historical_corpus_inventory.json"),
    )
    args = parser.parse_args()
    report = analyze_database(args.db)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "source_database_sha256": report["source_database_sha256"],
                "summary": report["summary"],
                "not_known_inspected_legacy_qa_complete_dates": report[
                    "not_known_inspected_legacy_qa_complete_dates"
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
