"""Batch production-lifecycle replay for Strategy A V3 signal dates.

Reads a Strategy A state-machine audit JSON, extracts the sessions that actually
emitted Strategy A signals, and replays exactly those dates through the existing
SimulationEngine / ReplayManifest / PositionManager path.

The historical database is treated as cache-only: this runner constructs the
HistoricalService without a broker gateway or instrument service, so targeted
provider fetches and historical option downloads are unavailable.  It never
changes Strategy A thresholds.

The report includes state-audit vs replay signal parity and aggregates only
TREND_PULLBACK manifests; Strategy B results from the shared simulation engine
are deliberately excluded from Strategy A metrics.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any

from services.historical.repository import HistoricalRepository
from services.historical.service import HistoricalService
from services.strategy.models import HistoricalReplaySource, SimulationRequest, StrategyName
from services.strategy.simulation import SimulationEngine


def _expected_signals(state_report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for session in state_report.get("sessions") or []:
        signals = list(session.get("signals") or [])
        if signals:
            result[str(session["date"])] = signals
    return result


def _strategy_a_manifests(simulation_result: Any) -> list[dict[str, Any]]:
    target = StrategyName.TREND_PULLBACK.value
    return [
        row
        for row in simulation_result.replay_manifests
        if str(row.get("strategy_id")) == target
    ]


def _signal_parity(
    expected: list[dict[str, Any]],
    manifests: list[dict[str, Any]],
) -> dict[str, Any]:
    expected_rows = [
        {
            "timestamp": row.get("timestamp"),
            "direction": row.get("option_type"),
            "entry": row.get("underlying_entry_price"),
            "stop": row.get("structural_stop"),
            "r_points": row.get("r_points"),
        }
        for row in expected
    ]
    actual_rows = [
        {
            "timestamp": row.get("simulated_entry_timestamp"),
            "direction": row.get("direction"),
            "entry": row.get("simulated_entry_price"),
            "stop": row.get("initial_structural_stop"),
            "r_points": row.get("initial_risk_points"),
        }
        for row in manifests
    ]

    def _timestamp_key(value: Any) -> str:
        raw = str(value or "")
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(f"signal parity timestamp must be timezone-aware: {raw}")
        return parsed.astimezone(timezone.utc).isoformat()

    def normalize(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
        normalized = []
        for row in rows:
            normalized.append(
                (
                    _timestamp_key(row.get("timestamp")),
                    str(row.get("direction")),
                    round(float(row.get("entry")), 8) if row.get("entry") is not None else None,
                    round(float(row.get("stop")), 8) if row.get("stop") is not None else None,
                    round(float(row.get("r_points")), 8) if row.get("r_points") is not None else None,
                )
            )
        return sorted(normalized)

    expected_norm = normalize(expected_rows)
    actual_norm = normalize(actual_rows)
    return {
        "match": expected_norm == actual_norm,
        "expected_count": len(expected_rows),
        "actual_count": len(actual_rows),
        "expected": expected_rows,
        "actual": actual_rows,
    }


def _record_time_key(row: dict[str, Any]) -> datetime:
    raw = (
        row.get("simulated_entry_timestamp")
        or row.get("entry_timestamp")
        or row.get("trading_date")
        or "1970-01-01T00:00:00+00:00"
    )
    value = str(raw)
    if len(value) == 10:
        value += "T00:00:00+00:00"
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = sorted(
        [
            row
            for row in records
            if row.get("lifecycle_status") == "RESOLVED"
            and row.get("realized_r") is not None
        ],
        key=_record_time_key,
    )
    unresolved = [row for row in records if row.get("lifecycle_status") != "RESOLVED"]
    ambiguous = [row for row in records if bool(row.get("ambiguous"))]
    rs = [float(row["realized_r"]) for row in resolved]
    winners = [value for value in rs if value > 0]
    losers = [value for value in rs if value < 0]

    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    max_loss_streak = 0
    current_loss_streak = 0
    for value in rs:
        equity += value
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
        if value < 0:
            current_loss_streak += 1
            max_loss_streak = max(max_loss_streak, current_loss_streak)
        else:
            current_loss_streak = 0

    option_status = Counter(str(row.get("option_data_status") or "UNKNOWN") for row in records)
    exits = Counter(str(row.get("exit_reason") or "UNRESOLVED") for row in records)
    directions = Counter(str(row.get("direction") or "UNKNOWN") for row in records)

    return {
        "signals": len(records),
        "resolved": len(resolved),
        "unresolved": len(unresolved),
        "ambiguous": len(ambiguous),
        "winners": len(winners),
        "losers": len(losers),
        "breakeven": sum(1 for value in rs if value == 0),
        "win_rate_pct": round(len(winners) / len(resolved) * 100, 2) if resolved else 0.0,
        "sum_realized_r": round(sum(rs), 6),
        "mean_realized_r": round(mean(rs), 6) if rs else None,
        "median_realized_r": (
            round(sorted(rs)[len(rs) // 2], 6)
            if rs and len(rs) % 2 == 1
            else round((sorted(rs)[len(rs)//2 - 1] + sorted(rs)[len(rs)//2]) / 2, 6)
            if rs
            else None
        ),
        "average_winner_r": round(mean(winners), 6) if winners else None,
        "average_loser_r": round(mean(losers), 6) if losers else None,
        "profit_factor_r": (
            round(sum(winners) / abs(sum(losers)), 6)
            if losers and sum(losers) != 0
            else None
        ),
        "max_drawdown_r": round(max_drawdown, 6),
        "max_consecutive_losses": max_loss_streak,
        "direction_counts": dict(sorted(directions.items())),
        "exit_reason_counts": dict(sorted(exits.items())),
        "option_data_status_counts": dict(sorted(option_status.items())),
    }


async def run_batch(
    state_report_path: Path,
    *,
    db_path: Path,
    source: HistoricalReplaySource,
) -> dict[str, Any]:
    state_report = json.loads(state_report_path.read_text(encoding="utf-8"))
    expected_by_date = _expected_signals(state_report)

    historical = HistoricalService(
        repository=HistoricalRepository(db_path),
        broker_gateway=None,
        instrument_service=None,
    )
    engine = SimulationEngine(historical_service=historical)

    sessions: list[dict[str, Any]] = []
    all_records: list[dict[str, Any]] = []
    parity_failures: list[str] = []

    for date_str in expected_by_date:
        result = await engine.run_day_simulation(
            SimulationRequest(
                date=date_str,
                instrument_id="INST-NIFTY-INDEX",
                historical_source=source,
                bypass_entry_window=False,
            )
        )
        manifests = _strategy_a_manifests(result)
        parity = _signal_parity(expected_by_date[date_str], manifests)
        if not parity["match"]:
            parity_failures.append(date_str)

        all_records.extend(manifests)
        sessions.append(
            {
                "date": date_str,
                "parity": parity,
                "strategy_a_manifest_count": len(manifests),
                "strategy_a_records": manifests,
                "simulation_limitation": result.limitation,
                "source_diagnostics": (
                    result.replay_metadata.get("data_fingerprint", {})
                    .get("source_diagnostics", {})
                ),
                "shared_engine_total_trades_all_strategies": result.total_trades,
            }
        )

    return {
        "report_type": "STRATEGY_A_V3_PRODUCTION_LIFECYCLE_BATCH",
        "state_report": str(state_report_path),
        "db_path": str(db_path.resolve()),
        "source": source.value,
        "signal_dates_requested": list(expected_by_date),
        "signal_dates_count": len(expected_by_date),
        "signal_parity": {
            "all_match": not parity_failures,
            "failure_dates": parity_failures,
        },
        "strategy_a": _aggregate(all_records),
        "records": all_records,
        "sessions": sessions,
        "production_thresholds_changed": False,
        "broker_called": False,
        "historical_provider_fetch_enabled": False,
        "notes": [
            "Only TREND_PULLBACK manifests are included in Strategy A aggregate metrics.",
            "SimulationEngine still evaluates Strategy B internally because the production replay endpoint is shared.",
            "Option data is normally unavailable in this cache-only CLI unless already represented by replay data loaded through the engine; underlying PositionManager lifecycle remains authoritative for R.",
        ],
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Batch production lifecycle replay for Strategy A V3 state-machine signal dates"
    )
    parser.add_argument(
        "--state-report",
        type=Path,
        default=Path("data") / "strategy_a_v3_state_machine_402.json",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path("data") / "market" / "historical.db",
    )
    parser.add_argument(
        "--source",
        choices=("BREEZE", "KITE", "LIVE", "MIXED"),
        default="BREEZE",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data") / "strategy_a_v3_lifecycle_13.json",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    report = asyncio.run(
        run_batch(
            args.state_report,
            db_path=args.db_path,
            source=HistoricalReplaySource(args.source),
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "report_type": report["report_type"],
                "signal_dates_count": report["signal_dates_count"],
                "signal_parity": report["signal_parity"],
                "strategy_a": report["strategy_a"],
                "output": str(args.output),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
