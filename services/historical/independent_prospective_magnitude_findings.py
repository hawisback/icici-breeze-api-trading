"""Analyze the frozen prospective NIFTY movement-magnitude replication.

The analyzer is locked until the full prospective window has completed. It
scores only the predeclared predictor, target, chronological blocks, Spearman
statistics, and session-cluster bootstrap gate. No quartile, threshold,
directional, P&L, or implementation analysis is produced.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from services.historical.independent_prospective_magnitude_protocol import (
    BLOCKS,
    BOOTSTRAP,
    COLLECTION_AND_SCORING_POLICY,
    CONTRACT_BY_DATE,
    EXPECTED_BARS_PER_SESSION,
    EXPECTED_ROWS,
    EXPECTED_SCORABLE_EVENTS,
    PROTOCOL_VERSION,
    REPLICATION_GATE,
    SESSION_DATES,
    SESSION_START,
    TERMINAL_OUTCOMES,
)

IST = ZoneInfo("Asia/Kolkata")
RESEARCH_TYPE = "NIFTY_PROSPECTIVE_MAGNITUDE_FINDINGS_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _full_sample_completion() -> datetime:
    return datetime.fromisoformat(
        COLLECTION_AND_SCORING_POLICY["full_sample_not_complete_before"]
    ).astimezone(IST)


def assert_scoring_window_open(now: datetime | None = None) -> None:
    current = now or datetime.now(IST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=IST)
    else:
        current = current.astimezone(IST)
    if current < _full_sample_completion():
        raise RuntimeError(
            "Prospective scoring is locked until the full frozen sample has "
            f"completed at {_full_sample_completion().isoformat()}."
        )


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) != len(y) or len(x) < 2:
        raise ValueError("Spearman inputs must have equal length >= 2")
    rx = pd.Series(x).rank(method="average").to_numpy(dtype=float)
    ry = pd.Series(y).rank(method="average").to_numpy(dtype=float)
    if float(np.std(rx)) == 0.0 or float(np.std(ry)) == 0.0:
        raise ValueError("Spearman input has zero rank variance")
    return float(np.corrcoef(rx, ry)[0, 1])


def _block_for_day(day: str) -> str:
    for name, days in BLOCKS.items():
        if day in days:
            return name
    raise ValueError(f"session {day} is not in frozen block map")


def _expected_timestamps(day: str) -> list[pd.Timestamp]:
    start = pd.Timestamp(f"{day}T{SESSION_START}:00+05:30")
    return [
        start + pd.Timedelta(minutes=5 * i)
        for i in range(EXPECTED_BARS_PER_SESSION)
    ]


def _validate(payload: dict[str, Any]) -> pd.DataFrame:
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("protocol version changed")
    if payload.get("provider") != "BREEZE":
        raise ValueError("provider must remain BREEZE")
    if list(payload.get("session_dates") or []) != SESSION_DATES:
        raise ValueError("session dates differ from frozen protocol")
    if dict(payload.get("contract_by_date") or {}) != CONTRACT_BY_DATE:
        raise ValueError("contract-by-date roll schedule changed")
    if int(payload.get("bars_per_session", -1)) != EXPECTED_BARS_PER_SESSION:
        raise ValueError("bars-per-session changed")
    if int(payload.get("rows", -1)) != EXPECTED_ROWS:
        raise ValueError("market row count changed")

    quality = payload.get("quality") or {}
    required_quality = {
        "sessions": len(SESSION_DATES),
        "rows": EXPECTED_ROWS,
        "complete_77_bar_sessions": len(SESSION_DATES),
        "duplicate_rows": 0,
        "invalid_ohlc_rows": 0,
        "missing_volume_rows": 0,
        "missing_open_interest_rows": 0,
        "nonpositive_volume_rows": 0,
        "nonpositive_open_interest_rows": 0,
        "wrong_contract_rows": 0,
    }
    for key, expected in required_quality.items():
        if quality.get(key) != expected:
            raise ValueError(
                f"market quality {key} expected {expected}, got {quality.get(key)}"
            )

    rows = list(payload.get("futures_rows") or [])
    if len(rows) != EXPECTED_ROWS:
        raise ValueError("futures_rows count changed")

    frame = pd.DataFrame(rows)
    required_cols = {
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "open_interest",
        "instrument",
        "futures_expiry",
        "source",
    }
    missing = sorted(required_cols - set(frame.columns))
    if missing:
        raise ValueError(f"market rows missing columns: {missing}")

    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True).dt.tz_convert(
        "Asia/Kolkata"
    )
    frame["date"] = frame["timestamp"].dt.date.astype(str)
    frame = frame.sort_values(["date", "timestamp"]).reset_index(drop=True)

    if frame["timestamp"].duplicated().any():
        raise ValueError("duplicate timestamps found")
    if set(frame["date"]) != set(SESSION_DATES):
        raise ValueError("market dates differ from frozen sessions")
    if not frame["source"].eq("BREEZE").all():
        raise ValueError("all market rows must be sourced from BREEZE")

    numeric_cols = ["open", "high", "low", "close", "volume", "open_interest"]
    for column in numeric_cols:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
        if not np.isfinite(frame[column].to_numpy(dtype=float)).all():
            raise ValueError(f"non-finite {column} values found")

    if not (
        (frame["low"] <= frame["open"])
        & (frame["open"] <= frame["high"])
        & (frame["low"] <= frame["close"])
        & (frame["close"] <= frame["high"])
    ).all():
        raise ValueError("invalid OHLC rows found")
    if not (frame["volume"] > 0).all():
        raise ValueError("nonpositive volume rows found")
    if not (frame["open_interest"] > 0).all():
        raise ValueError("nonpositive open-interest rows found")

    for day in SESSION_DATES:
        group = frame.loc[frame["date"] == day].sort_values("timestamp")
        if len(group) != EXPECTED_BARS_PER_SESSION:
            raise ValueError(f"{day} expected 77 bars, got {len(group)}")
        actual_ts = group["timestamp"].tolist()
        if actual_ts != _expected_timestamps(day):
            raise ValueError(f"{day} exact 5-minute timestamp shape changed")
        expiry = CONTRACT_BY_DATE[day]
        if not group["futures_expiry"].eq(expiry).all():
            raise ValueError(f"{day} contains wrong futures expiry")
        if not group["instrument"].eq(f"NIFTY FUT {expiry}").all():
            raise ValueError(f"{day} contains wrong instrument label")

    return frame


def _build_events(frame: pd.DataFrame) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for day in SESSION_DATES:
        group = (
            frame.loc[frame["date"] == day]
            .sort_values("timestamp")
            .reset_index(drop=True)
        )
        if len(group) != EXPECTED_BARS_PER_SESSION:
            raise ValueError(f"{day} expected 77 bars, got {len(group)}")

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
                    "block": _block_for_day(day),
                    "timestamp": group.iloc[i]["timestamp"].isoformat(),
                    "trailing_30m_range_bps": predictor,
                    "next_30m_max_absolute_excursion_bps": target,
                }
            )

    events = pd.DataFrame(records)
    if len(events) != EXPECTED_SCORABLE_EVENTS:
        raise ValueError(
            f"expected {EXPECTED_SCORABLE_EVENTS} scorable events, got {len(events)}"
        )
    return events


def _bootstrap(events: pd.DataFrame) -> tuple[float, float]:
    rng = np.random.default_rng(int(BOOTSTRAP["seed"]))
    by_day = {
        day: events.loc[
            events["date"] == day,
            [
                "trailing_30m_range_bps",
                "next_30m_max_absolute_excursion_bps",
            ],
        ].to_numpy(dtype=float)
        for day in SESSION_DATES
    }
    draws = np.empty(int(BOOTSTRAP["draws"]), dtype=float)
    for i in range(len(draws)):
        sampled_days = rng.choice(
            SESSION_DATES,
            size=len(SESSION_DATES),
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
    scorable_events: int,
    pooled_spearman: float,
    block_spearman: dict[str, float],
    bootstrap_low: float,
) -> list[str]:
    failures: list[str] = []
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


def analyze(
    payload: dict[str, Any],
    *,
    source_sha256: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    assert_scoring_window_open(now)
    frame = _validate(payload)
    events = _build_events(frame)

    x = events["trailing_30m_range_bps"].to_numpy(dtype=float)
    y = events["next_30m_max_absolute_excursion_bps"].to_numpy(dtype=float)
    pooled = _spearman(x, y)
    block_rho = {
        block: _spearman(
            group["trailing_30m_range_bps"].to_numpy(dtype=float),
            group["next_30m_max_absolute_excursion_bps"].to_numpy(dtype=float),
        )
        for block, group in events.groupby("block", sort=True)
    }
    bootstrap_low, bootstrap_high = _bootstrap(events)
    failures = _evaluate_gate(
        scorable_events=len(events),
        pooled_spearman=pooled,
        block_spearman=block_rho,
        bootstrap_low=bootstrap_low,
    )
    passed = not failures

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "research_only": True,
        "prospective_sample": True,
        "candidate_frozen": False,
        "blind_validation_opened": False,
        "implementation_allowed": False,
        "source_market_sha256": source_sha256,
        "sessions": len(SESSION_DATES),
        "market_rows": len(frame),
        "scorable_events": len(events),
        "predictor": "trailing_30m_range_bps",
        "target": "next_30m_max_absolute_excursion_bps",
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
            "PROSPECTIVE_DESCRIPTIVE_MAGNITUDE_RELATIONSHIP_REPLICATED"
            if passed
            else "PROSPECTIVE_DESCRIPTIVE_MAGNITUDE_RELATIONSHIP_NOT_REPLICATED"
        ),
        "terminal_outcome": (
            TERMINAL_OUTCOMES["if_pass"]
            if passed
            else TERMINAL_OUTCOMES["if_fail"]
        ),
        "interpretation": (
            "This is a non-directional prospective descriptive replication only. "
            "A pass does not create a trading candidate, threshold, sizing rule, "
            "blind-validation authorization, or implementation authorization."
        ),
        "guardrails": {
            "directional_claim": False,
            "pnl_scored": False,
            "threshold_selected": False,
            "quartile_analysis_produced": False,
            "candidate_frozen": False,
            "blind_validation_allowed": False,
            "implementation_allowed": False,
            "no_followup_parameter_tuning": True,
            "strategy_d_remains_paused": True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze frozen prospective NIFTY magnitude replication"
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/independent_prospective_magnitude_findings.json"),
    )
    args = parser.parse_args()
    source_sha = _sha256(args.market)
    payload = json.loads(args.market.read_text(encoding="utf-8"))
    report = analyze(payload, source_sha256=source_sha)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "source_market_sha256": source_sha,
                "decision": report["decision"],
                "terminal_outcome": report["terminal_outcome"],
                "replication_gate": report["replication_gate"],
                "results": report["results"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
