"""Run the frozen retrospective NIFTY movement-magnitude robustness study."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from services.historical.independent_retrospective_magnitude_protocol import (
    BLOCK_POLICY,
    BOOTSTRAP,
    CORPUS_ROLE,
    EXPECTED_BARS_PER_SESSION,
    EXPECTED_SCORABLE_EVENTS_PER_SESSION,
    FUTURES_CONTRACT_PERIODS,
    GUARDRAILS,
    PREDICTOR,
    PROTOCOL_VERSION,
    QA_POLICY,
    REPLICATION_GATE,
    SESSION_LAST_BAR,
    SESSION_START,
    TARGET,
    WINDOW_END,
    WINDOW_START,
)

IST = ZoneInfo("Asia/Kolkata")
RESEARCH_TYPE = "NIFTY_RETROSPECTIVE_MAGNITUDE_ROBUSTNESS_FINDINGS_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) != len(y) or len(x) < 2:
        raise ValueError("Spearman inputs must have equal length >= 2")
    rx = pd.Series(x).rank(method="average").to_numpy(dtype=float)
    ry = pd.Series(y).rank(method="average").to_numpy(dtype=float)
    if float(np.std(rx)) == 0.0 or float(np.std(ry)) == 0.0:
        raise ValueError("Spearman input has zero rank variance")
    return float(np.corrcoef(rx, ry)[0, 1])


def _period_for_day(day: str) -> dict[str, str]:
    for period in FUTURES_CONTRACT_PERIODS:
        if period["first_date"] <= day <= period["last_date"]:
            return period
    raise ValueError(f"{day} is outside the frozen contract schedule")


def _expected_timestamps(day: str) -> list[pd.Timestamp]:
    start = pd.Timestamp(f"{day}T{SESSION_START}:00+05:30")
    return [
        start + pd.Timedelta(minutes=5 * i)
        for i in range(EXPECTED_BARS_PER_SESSION)
    ]


def _read_breeze_rows(db_path: Path) -> pd.DataFrame:
    if not db_path.exists():
        raise FileNotFoundError(f"historical database not found: {db_path}")

    columns = [
        "instrument_id",
        "interval",
        "start_time",
        "end_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "open_interest",
        "source",
    ]
    rows: list[dict[str, Any]] = []
    with sqlite3.connect(str(db_path)) as conn:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='historical_candles'"
        ).fetchone()
        if not table:
            raise ValueError("historical_candles table not found")

        for period in FUTURES_CONTRACT_PERIODS:
            cursor = conn.execute(
                """
                SELECT instrument_id, interval, start_time, end_time,
                       open, high, low, close, volume, open_interest, source
                FROM historical_candles
                WHERE instrument_id = ?
                  AND interval = '5m'
                  AND source = 'BREEZE'
                ORDER BY start_time ASC
                """,
                (period["instrument_id"],),
            )
            for values in cursor.fetchall():
                row = dict(zip(columns, values))
                rows.append(row)

    if not rows:
        raise ValueError("no frozen-window Breeze NIFTY futures rows found")

    frame = pd.DataFrame(rows)
    frame["timestamp"] = pd.to_datetime(
        frame["start_time"], utc=True, errors="raise"
    ).dt.tz_convert("Asia/Kolkata")
    frame["date"] = frame["timestamp"].dt.date.astype(str)

    keep = (
        (frame["date"] >= WINDOW_START)
        & (frame["date"] <= WINDOW_END)
        & frame["timestamp"].dt.time.between(
            time.fromisoformat(SESSION_START),
            time.fromisoformat(SESSION_LAST_BAR),
            inclusive="both",
        )
    )
    frame = frame.loc[keep].copy()
    frame = frame.sort_values(["date", "timestamp"]).reset_index(drop=True)
    return frame


def _weekday_dates() -> list[str]:
    start = date.fromisoformat(WINDOW_START)
    end = date.fromisoformat(WINDOW_END)
    result: list[str] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            result.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return result


def _qa_sessions(
    frame: pd.DataFrame,
) -> tuple[dict[str, pd.DataFrame], list[dict[str, Any]], list[str]]:
    accepted: dict[str, pd.DataFrame] = {}
    rejected: list[dict[str, Any]] = []

    present_dates = set(frame["date"].astype(str))
    no_row_weekdays = sorted(set(_weekday_dates()) - present_dates)

    for day, raw_group in frame.groupby("date", sort=True):
        day = str(day)
        group = raw_group.sort_values("timestamp").reset_index(drop=True)
        reasons: list[str] = []

        period = _period_for_day(day)
        if not group["instrument_id"].eq(period["instrument_id"]).all():
            reasons.append("wrong_contract")
        if not group["source"].eq("BREEZE").all():
            reasons.append("non_breeze_source")
        if not group["interval"].eq("5m").all():
            reasons.append("wrong_interval")
        if group["timestamp"].duplicated().any():
            reasons.append("duplicate_timestamp")

        if len(group) != EXPECTED_BARS_PER_SESSION:
            reasons.append(
                f"bar_count_{len(group)}_expected_{EXPECTED_BARS_PER_SESSION}"
            )
        elif group["timestamp"].tolist() != _expected_timestamps(day):
            reasons.append("timestamp_shape")

        for column in ("open", "high", "low", "close"):
            group[column] = pd.to_numeric(group[column], errors="coerce")
        numeric = group[["open", "high", "low", "close"]].to_numpy(dtype=float)
        if not np.isfinite(numeric).all():
            reasons.append("non_finite_ohlc")
        elif not (numeric > 0.0).all():
            reasons.append("nonpositive_price")
        elif not (
            (group["low"] <= group["open"])
            & (group["open"] <= group["high"])
            & (group["low"] <= group["close"])
            & (group["close"] <= group["high"])
        ).all():
            reasons.append("invalid_ohlc")

        if reasons:
            rejected.append(
                {
                    "date": day,
                    "instrument_id": period["instrument_id"],
                    "observed_rows": int(len(group)),
                    "reasons": reasons,
                }
            )
        else:
            accepted[day] = group

    return accepted, rejected, no_row_weekdays


def _chronological_blocks(session_dates: list[str]) -> dict[str, list[str]]:
    n = len(session_dates)
    base, remainder = divmod(n, 3)
    sizes = [base + (1 if i < remainder else 0) for i in range(3)]
    result: dict[str, list[str]] = {}
    cursor = 0
    for i, size in enumerate(sizes, start=1):
        result[f"block{i}"] = session_dates[cursor : cursor + size]
        cursor += size
    if cursor != n:
        raise AssertionError("chronological block split did not consume all sessions")
    return result


def _build_events(
    sessions: dict[str, pd.DataFrame],
    blocks: dict[str, list[str]],
) -> pd.DataFrame:
    block_by_day = {
        day: block for block, days in blocks.items() for day in days
    }
    records: list[dict[str, Any]] = []

    for day in sorted(sessions):
        group = sessions[day]
        if len(group) != EXPECTED_BARS_PER_SESSION:
            raise ValueError(f"{day} session shape changed after QA")
        for i in range(5, len(group) - 6):
            trailing = group.iloc[i - 5 : i + 1]
            future = group.iloc[i + 1 : i + 7]
            close = float(group.iloc[i]["close"])
            predictor = (
                (
                    float(trailing["high"].max())
                    - float(trailing["low"].min())
                )
                / close
                * 10000.0
            )
            target = (
                max(
                    float(future["high"].max()) - close,
                    close - float(future["low"].min()),
                )
                / close
                * 10000.0
            )
            records.append(
                {
                    "date": day,
                    "block": block_by_day[day],
                    "timestamp": group.iloc[i]["timestamp"].isoformat(),
                    PREDICTOR["name"]: predictor,
                    TARGET["name"]: target,
                }
            )

    events = pd.DataFrame(records)
    expected = len(sessions) * EXPECTED_SCORABLE_EVENTS_PER_SESSION
    if len(events) != expected:
        raise ValueError(f"expected {expected} scorable events, got {len(events)}")
    return events


def _bootstrap(events: pd.DataFrame, session_dates: list[str]) -> tuple[float, float]:
    rng = np.random.default_rng(int(BOOTSTRAP["seed"]))
    x_name = str(PREDICTOR["name"])
    y_name = str(TARGET["name"])
    by_day = {
        day: events.loc[
            events["date"] == day,
            [x_name, y_name],
        ].to_numpy(dtype=float)
        for day in session_dates
    }
    draws = np.empty(int(BOOTSTRAP["draws"]), dtype=float)
    for i in range(len(draws)):
        sampled_days = rng.choice(
            session_dates,
            size=len(session_dates),
            replace=True,
        )
        sample = np.concatenate(
            [by_day[str(day)] for day in sampled_days],
            axis=0,
        )
        draws[i] = _spearman(sample[:, 0], sample[:, 1])
    return (
        float(np.quantile(draws, 0.025)),
        float(np.quantile(draws, 0.975)),
    )


def _evaluate_gate(
    *,
    sessions: int,
    scorable_events: int,
    pooled_spearman: float,
    block_spearman: dict[str, float],
    bootstrap_low: float,
) -> list[str]:
    failures: list[str] = []
    if sessions < int(REPLICATION_GATE["minimum_complete_sessions"]):
        failures.append("minimum_complete_sessions")
    if scorable_events < int(REPLICATION_GATE["minimum_scorable_events"]):
        failures.append("minimum_scorable_events")
    if pooled_spearman <= 0.0:
        failures.append("pooled_spearman_not_positive")
    if len(block_spearman) != 3 or any(
        value <= 0.0 for value in block_spearman.values()
    ):
        failures.append("block_spearman_not_positive_in_all_three_blocks")
    if bootstrap_low <= 0.0:
        failures.append("bootstrap_lower_bound_not_positive")
    return failures


def analyze_database(db_path: Path) -> dict[str, Any]:
    frame = _read_breeze_rows(db_path)
    accepted, rejected, no_row_weekdays = _qa_sessions(frame)
    session_dates = sorted(accepted)

    if len(session_dates) < int(QA_POLICY["minimum_complete_sessions"]):
        return {
            "research_type": RESEARCH_TYPE,
            "protocol_version": PROTOCOL_VERSION,
            "corpus_role": CORPUS_ROLE,
            "research_only": True,
            "source_database": str(db_path),
            "source_database_sha256": _sha256(db_path),
            "window": [WINDOW_START, WINDOW_END],
            "qa": {
                "passed": False,
                "complete_sessions": len(session_dates),
                "minimum_complete_sessions": int(
                    QA_POLICY["minimum_complete_sessions"]
                ),
                "accepted_session_dates": session_dates,
                "rejected_sessions": rejected,
                "weekday_dates_with_no_breeze_rows": no_row_weekdays,
            },
            "results": None,
            "replication_gate": {
                "passed": False,
                "failures": ["minimum_complete_sessions"],
            },
            "decision": "RETROSPECTIVE_MAGNITUDE_ROBUSTNESS_QA_FAILED",
            "guardrails": GUARDRAILS,
        }

    blocks = _chronological_blocks(session_dates)
    events = _build_events(accepted, blocks)
    x_name = str(PREDICTOR["name"])
    y_name = str(TARGET["name"])
    x = events[x_name].to_numpy(dtype=float)
    y = events[y_name].to_numpy(dtype=float)

    pooled = _spearman(x, y)
    block_rho = {
        block: _spearman(
            group[x_name].to_numpy(dtype=float),
            group[y_name].to_numpy(dtype=float),
        )
        for block, group in events.groupby("block", sort=True)
    }
    bootstrap_low, bootstrap_high = _bootstrap(events, session_dates)
    failures = _evaluate_gate(
        sessions=len(session_dates),
        scorable_events=len(events),
        pooled_spearman=pooled,
        block_spearman=block_rho,
        bootstrap_low=bootstrap_low,
    )
    passed = not failures

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "corpus_role": CORPUS_ROLE,
        "research_only": True,
        "retrospective": True,
        "prospective_validation": False,
        "source_database": str(db_path),
        "source_database_sha256": _sha256(db_path),
        "provider": "BREEZE",
        "window": [WINDOW_START, WINDOW_END],
        "session_semantics": {
            "start": SESSION_START,
            "last_bar": SESSION_LAST_BAR,
            "bars_per_session": EXPECTED_BARS_PER_SESSION,
            "optional_15_30_bar_ignored": True,
        },
        "qa": {
            "passed": True,
            "complete_sessions": len(session_dates),
            "accepted_session_dates": session_dates,
            "rejected_sessions": rejected,
            "weekday_dates_with_no_breeze_rows": no_row_weekdays,
            "contract_periods": FUTURES_CONTRACT_PERIODS,
        },
        "chronological_blocks": blocks,
        "scorable_events": len(events),
        "predictor": PREDICTOR,
        "target": TARGET,
        "results": {
            "pooled_spearman": pooled,
            "block_spearman": block_rho,
            "session_cluster_bootstrap_95pct": [
                bootstrap_low,
                bootstrap_high,
            ],
        },
        "replication_gate": {
            "passed": passed,
            "failures": failures,
        },
        "decision": (
            "RETROSPECTIVE_MAGNITUDE_RELATIONSHIP_ROBUST"
            if passed
            else "RETROSPECTIVE_MAGNITUDE_RELATIONSHIP_NOT_ROBUST"
        ),
        "interpretation": (
            "Historical robustness evidence only. It does not replace the frozen "
            "prospective replication and cannot create a trading candidate, "
            "threshold, sizing rule, blind-validation authorization, or "
            "implementation authorization."
        ),
        "guardrails": GUARDRAILS,
        "block_policy": BLOCK_POLICY,
        "bootstrap": BOOTSTRAP,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run frozen retrospective NIFTY magnitude robustness study"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path("data/market/historical.db"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "data/independent_retrospective_magnitude_findings.json"
        ),
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
                "qa": report["qa"],
                "scorable_events": report.get("scorable_events"),
                "results": report.get("results"),
                "replication_gate": report["replication_gate"],
                "decision": report["decision"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
