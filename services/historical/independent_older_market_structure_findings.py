"""Score the frozen 2022-2024 replication of two NIFTY range relationships."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from services.historical.independent_older_market_structure_replication_protocol import (
    BOOTSTRAP,
    PROTOCOL_VERSION,
    RELATIONSHIPS,
    REPLICATION_GATE,
    WINDOW,
)

RESEARCH_TYPE = "NIFTY_BREEZE_OLDER_MARKET_STRUCTURE_REPLICATION_FINDINGS_V1"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _spearman(x: np.ndarray, y: np.ndarray) -> float:
    rx = pd.Series(x).rank(method="average").to_numpy(dtype=float)
    ry = pd.Series(y).rank(method="average").to_numpy(dtype=float)
    if len(rx) < 3 or float(np.std(rx)) == 0.0 or float(np.std(ry)) == 0.0:
        raise ValueError("invalid Spearman inputs")
    return float(np.corrcoef(rx, ry)[0, 1])


def _validate_market(payload: dict[str, Any]) -> pd.DataFrame:
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("market protocol version mismatch")
    if payload.get("provider") != "BREEZE":
        raise ValueError("market provider must be BREEZE")
    if payload.get("pattern_scoring_performed") is not False:
        raise ValueError("market artifact must be outcome-unscored")
    rows = list(payload.get("futures_rows") or [])
    if not rows:
        raise ValueError("market artifact has no futures rows")

    frame = pd.DataFrame(rows).copy()
    required = {"timestamp", "date", "open", "high", "low", "close", "futures_expiry", "source"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"market rows missing columns: {missing}")
    for col in ("open", "high", "low", "close"):
        frame[col] = pd.to_numeric(frame[col], errors="raise")
    if not np.isfinite(frame[["open", "high", "low", "close"]].to_numpy(dtype=float)).all():
        raise ValueError("market contains non-finite OHLC")
    if (frame[["open", "high", "low", "close"]] <= 0.0).any().any():
        raise ValueError("market contains nonpositive OHLC")
    if not frame["source"].eq("BREEZE").all():
        raise ValueError("market contains non-Breeze rows")
    if not frame.groupby("date").size().eq(int(WINDOW["bars_per_session"])).all():
        raise ValueError("market contains non-75-bar session")
    return frame.sort_values(["date", "timestamp"]).reset_index(drop=True)


def _build_sessions(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for day, group in frame.groupby("date", sort=True):
        group = group.sort_values("timestamp")
        session_open = float(group.iloc[0]["open"])
        session_high = float(group["high"].max())
        session_low = float(group["low"].min())
        session_close = float(group.iloc[-1]["close"])
        first6 = group.iloc[:6]
        remaining = group.iloc[6:]
        rows.append(
            {
                "date": str(day),
                "year": int(str(day)[:4]),
                "session_close": session_close,
                "session_high_low_range_bps": (
                    (session_high - session_low) / session_open * 10000.0
                ),
                "first_30m_high_low_range_bps": (
                    (float(first6["high"].max()) - float(first6["low"].min()))
                    / session_open * 10000.0
                ),
                "post_09_40_remaining_session_high_low_range_bps": (
                    (float(remaining["high"].max()) - float(remaining["low"].min()))
                    / session_open * 10000.0
                ),
            }
        )
    sessions = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    sessions["previous_session_high_low_range_bps"] = sessions[
        "session_high_low_range_bps"
    ].shift(1)
    return sessions


def _bootstrap_ci(x: np.ndarray, y: np.ndarray) -> list[float]:
    rng = np.random.default_rng(int(BOOTSTRAP["seed"]))
    n = len(x)
    draws = np.empty(int(BOOTSTRAP["draws"]), dtype=float)
    for i in range(len(draws)):
        idx = rng.integers(0, n, size=n)
        try:
            draws[i] = _spearman(x[idx], y[idx])
        except ValueError:
            draws[i] = np.nan
    draws = draws[np.isfinite(draws)]
    return [
        float(np.quantile(draws, 0.025)),
        float(np.quantile(draws, 0.975)),
    ]


def _score_relationship(
    sessions: pd.DataFrame,
    predictor: str,
    outcome: str,
) -> dict[str, Any]:
    usable = sessions[["date", "year", predictor, outcome]].dropna().copy()
    pooled = _spearman(
        usable[predictor].to_numpy(dtype=float),
        usable[outcome].to_numpy(dtype=float),
    )
    by_year: dict[str, float] = {}
    for year in (2022, 2023, 2024):
        chunk = usable.loc[usable["year"] == year]
        by_year[str(year)] = _spearman(
            chunk[predictor].to_numpy(dtype=float),
            chunk[outcome].to_numpy(dtype=float),
        )
    ci = _bootstrap_ci(
        usable[predictor].to_numpy(dtype=float),
        usable[outcome].to_numpy(dtype=float),
    )
    failures: list[str] = []
    if len(sessions) < int(REPLICATION_GATE["minimum_complete_sessions"]):
        failures.append("minimum_complete_sessions")
    if pooled <= 0.0:
        failures.append("pooled_spearman_not_positive")
    if any(value <= 0.0 for value in by_year.values()):
        failures.append("calendar_year_spearman_not_positive_for_all_three_years")
    if ci[0] <= 0.0:
        failures.append("bootstrap_lower_bound_not_positive")
    return {
        "observations": int(len(usable)),
        "pooled_spearman": pooled,
        "calendar_year_spearman": by_year,
        "session_cluster_bootstrap_95pct": ci,
        "replication_gate": {
            "passed": not failures,
            "failures": failures,
        },
    }


def analyze_market(payload: dict[str, Any]) -> dict[str, Any]:
    frame = _validate_market(payload)
    sessions = _build_sessions(frame)
    results: dict[str, Any] = {}
    for name, spec in RELATIONSHIPS.items():
        results[name] = _score_relationship(
            sessions,
            str(spec["predictor"]),
            str(spec["outcome"]),
        )

    passed = [name for name, result in results.items() if result["replication_gate"]["passed"]]
    if len(passed) == 2:
        decision = "OLDER_HISTORICAL_BOTH_MARKET_STRUCTURE_RELATIONSHIPS_REPLICATED"
    elif passed == ["opening_range_vs_remaining_range"]:
        decision = "OLDER_HISTORICAL_OPENING_RANGE_RELATIONSHIP_ONLY_REPLICATED"
    elif passed == ["daily_range_persistence"]:
        decision = "OLDER_HISTORICAL_DAILY_RANGE_PERSISTENCE_ONLY_REPLICATED"
    else:
        decision = "OLDER_HISTORICAL_MARKET_STRUCTURE_RELATIONSHIPS_NOT_REPLICATED"

    return {
        "research_type": RESEARCH_TYPE,
        "protocol_version": PROTOCOL_VERSION,
        "research_only": True,
        "provider": "BREEZE",
        "complete_sessions": int(sessions["date"].nunique()),
        "first_date": str(sessions["date"].iloc[0]),
        "last_date": str(sessions["date"].iloc[-1]),
        "results": results,
        "decision": decision,
        "guardrails": {
            "feature_search": False,
            "posthoc_relationship_additions": False,
            "future_data_required": False,
            "pnl_scored": False,
            "implementation_allowed": False,
            "strategy_d_remains_paused": True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze frozen 2022-2024 Breeze market-structure replication"
    )
    parser.add_argument("--market", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/independent_older_market_structure_findings.json"),
    )
    args = parser.parse_args()
    payload = json.loads(args.market.read_text(encoding="utf-8"))
    report = analyze_market(payload)
    report["source_market_sha256"] = _sha256(args.market)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "output": str(args.output),
                "sha256": digest,
                "source_market_sha256": report["source_market_sha256"],
                "complete_sessions": report["complete_sessions"],
                "decision": report["decision"],
                "results": report["results"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
