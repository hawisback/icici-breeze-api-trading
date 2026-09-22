"""Read-only Strategy A V3 rule-funnel audit over cached historical data.

This module:
- opens the replay SQLite database in read-only mode,
- uses the authoritative Strategy A V3 defaults,
- evaluates only completed 15-minute NIFTY futures bars in the entry window,
- never calls a broker, writes candles, changes thresholds, or persists trades.

It is intentionally diagnostic. A READY directional evaluation means the
market-data gates can form a valid prospective setup; it is not itself a
persisted or executed trade.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from services.historical.strategy_a_data_audit import (
    ENTRY_FIRST_END,
    ENTRY_LAST_END,
    IST,
    OPTIONAL_CLOSE_MARKER,
    SESSION_START,
    WARMUP_CALENDAR_DAYS,
    _active_contract,
    _default_db_path,
    _latest_spot_session_dates,
    _load_rows,
    _open_read_only,
    _session_bounds,
)
from services.strategy.models import StrategyTunablesConfig
from services.strategy.strategies.trend_pullback import TrendPullbackStrategy


DATA_QUALITY_BLOCKERS = {
    "STALE_FUTURES_DATA",
    "FUTURES_DATA_UNAVAILABLE",
    "INCOMPLETE_FUTURES_DATA",
}


def _decision_bar_ends(day: date) -> list[datetime]:
    cursor = datetime.combine(day, ENTRY_FIRST_END, tzinfo=IST)
    last = datetime.combine(day, ENTRY_LAST_END, tzinfo=IST)
    result: list[datetime] = []
    while cursor <= last:
        result.append(cursor)
        cursor += timedelta(minutes=15)
    return result


def _condition_map(diag: Any) -> dict[str, Any]:
    return {condition.id: condition for condition in diag.conditions}


def _v2_payload(diag: Any) -> dict[str, Any]:
    phase_summary = diag.phase_summary or {}
    return phase_summary.get("strategy_a_v2") or {}


def _new_counter_row() -> dict[str, int]:
    return {"evaluated": 0, "passed": 0}


def _finalize_counter_rows(rows: dict[str, dict[str, int]]) -> dict[str, dict[str, float | int]]:
    result: dict[str, dict[str, float | int]] = {}
    for key, row in sorted(rows.items()):
        evaluated = int(row["evaluated"])
        passed = int(row["passed"])
        result[key] = {
            "evaluated": evaluated,
            "passed": passed,
            "pass_pct": round(passed / evaluated * 100, 2) if evaluated else 0.0,
        }
    return result


def _audit_session(conn: Any, day: date, *, source: str, config: StrategyTunablesConfig) -> dict[str, Any]:
    session_start_utc, session_end_utc = _session_bounds(day)
    session_futures = _load_rows(
        conn,
        start_utc=session_start_utc,
        end_utc=session_end_utc,
        source=source,
        instrument_like="INST-NIFTY-FUT-%",
    )
    active_contract = _active_contract(day, session_futures)
    if not active_contract:
        return {
            "date": day.isoformat(),
            "active_futures_contract": None,
            "completed_15m_decision_bars": 0,
            "directional_evaluations": 0,
            "ready_directional_evaluations": 0,
            "data_quality_counts": {"FUTURES_DATA_UNAVAILABLE": 1},
            "blocker_counts": {},
            "progressive_funnel": {},
            "gate_funnel": {},
            "component_funnel": {},
            "gate_reason_counts": {},
        }

    warmup_start = datetime.combine(
        day - timedelta(days=WARMUP_CALENDAR_DAYS),
        SESSION_START,
        tzinfo=IST,
    )
    history_end = datetime.combine(day, OPTIONAL_CLOSE_MARKER, tzinfo=IST) + timedelta(minutes=5)
    futures_history = _load_rows(
        conn,
        start_utc=warmup_start.astimezone(session_start_utc.tzinfo),
        end_utc=history_end.astimezone(session_start_utc.tzinfo),
        source=source,
        instrument_like="INST-NIFTY-FUT-%",
    )

    gate_rows: dict[str, dict[str, int]] = {}
    component_rows: dict[str, dict[str, int]] = {}
    blocker_counts: Counter[str] = Counter()
    data_quality_counts: Counter[str] = Counter()
    gate_reason_counts: dict[str, Counter[str]] = {
        "trend": Counter(),
        "confirmation": Counter(),
        "confluence": Counter(),
        "risk": Counter(),
    }
    progressive = Counter({
        "directional_evaluations": 0,
        "trend_pass": 0,
        "trend_and_confirmation_pass": 0,
        "trend_confirmation_confluence_pass": 0,
        "risk_ready": 0,
    })
    ready_by_direction: Counter[str] = Counter()
    completed_bar_timestamps: set[str] = set()

    strategy = TrendPullbackStrategy(config=config, allow_session_bypass=False)

    for bar_end in _decision_bar_ends(day):
        # Evaluate five minutes after the quarter-hour boundary so the just-
        # completed 15m futures bar is unambiguously the latest complete bar.
        as_of = bar_end + timedelta(minutes=5)
        available_futures = [
            candle for candle in futures_history
            if candle.end_time <= as_of
        ]
        diags = strategy.diagnose(
            SimpleNamespace(timestamp=as_of),
            [],
            [],
            futures_candles=available_futures,
        )
        for diag in diags:
            progressive["directional_evaluations"] += 1
            blocker = str(diag.key_blocker or "")
            if blocker in DATA_QUALITY_BLOCKERS:
                data_quality_counts[blocker] += 1
            elif blocker and blocker != "READY":
                blocker_counts[blocker] += 1

            payload = _v2_payload(diag)
            data_payload = payload.get("data") or {}
            completed_ts = data_payload.get("completed_candle_timestamp")
            if completed_ts:
                completed_bar_timestamps.add(str(completed_ts))

            conditions = _condition_map(diag)
            statuses: dict[str, bool] = {}
            for gate_id in ("trend", "confirmation", "confluence", "risk"):
                condition = conditions.get(gate_id)
                if condition is None:
                    continue
                if gate_id == "risk" and condition.gap_description == "WAITING_FOR_SETUP_PREREQUISITES":
                    continue
                row = gate_rows.setdefault(gate_id, _new_counter_row())
                row["evaluated"] += 1
                passed = condition.status == "PASSED"
                statuses[gate_id] = passed
                if passed:
                    row["passed"] += 1

            trend_payload = payload.get("trend") or {}
            confirmation_payload = payload.get("confirmation") or {}
            confluence_payload = payload.get("confluence") or {}
            risk_payload = payload.get("risk") or {}

            for gate_id, gate_payload in (
                ("trend", trend_payload),
                ("confirmation", confirmation_payload),
                ("confluence", confluence_payload),
                ("risk", risk_payload),
            ):
                reason = gate_payload.get("reason")
                if reason:
                    gate_reason_counts[gate_id][str(reason)] += 1

            for section_name, section_payload in (
                ("trend", trend_payload),
                ("confirmation", confirmation_payload),
                ("confluence", confluence_payload),
            ):
                for component, passed_value in (section_payload.get("components") or {}).items():
                    key = f"{section_name}.{component}"
                    row = component_rows.setdefault(key, _new_counter_row())
                    row["evaluated"] += 1
                    if bool(passed_value):
                        row["passed"] += 1

            trend_pass = bool(statuses.get("trend"))
            confirmation_pass = bool(statuses.get("confirmation"))
            confluence_pass = bool(statuses.get("confluence"))
            risk_pass = bool(statuses.get("risk"))
            if trend_pass:
                progressive["trend_pass"] += 1
            if trend_pass and confirmation_pass:
                progressive["trend_and_confirmation_pass"] += 1
            if trend_pass and confirmation_pass and confluence_pass:
                progressive["trend_confirmation_confluence_pass"] += 1
            if trend_pass and confirmation_pass and confluence_pass and risk_pass:
                progressive["risk_ready"] += 1
                ready_by_direction[str(diag.option_type.value)] += 1

    total = progressive["directional_evaluations"]
    progressive_report = {
        "directional_evaluations": total,
        "trend_pass": progressive["trend_pass"],
        "trend_pass_pct": round(progressive["trend_pass"] / total * 100, 2) if total else 0.0,
        "trend_and_confirmation_pass": progressive["trend_and_confirmation_pass"],
        "trend_and_confirmation_pass_pct": round(
            progressive["trend_and_confirmation_pass"] / total * 100, 2
        ) if total else 0.0,
        "trend_confirmation_confluence_pass": progressive["trend_confirmation_confluence_pass"],
        "trend_confirmation_confluence_pass_pct": round(
            progressive["trend_confirmation_confluence_pass"] / total * 100, 2
        ) if total else 0.0,
        "risk_ready": progressive["risk_ready"],
        "risk_ready_pct": round(progressive["risk_ready"] / total * 100, 2) if total else 0.0,
    }

    return {
        "date": day.isoformat(),
        "active_futures_contract": active_contract,
        "completed_15m_decision_bars": len(completed_bar_timestamps),
        "directional_evaluations": total,
        "ready_directional_evaluations": progressive["risk_ready"],
        "ready_by_direction": dict(sorted(ready_by_direction.items())),
        "data_quality_counts": dict(data_quality_counts.most_common()),
        "blocker_counts": dict(blocker_counts.most_common()),
        "progressive_funnel": progressive_report,
        "gate_funnel": _finalize_counter_rows(gate_rows),
        "component_funnel": _finalize_counter_rows(component_rows),
        "gate_reason_counts": {
            gate_id: dict(counter.most_common())
            for gate_id, counter in gate_reason_counts.items()
        },
    }


def _merge_rows(
    target: dict[str, dict[str, int]],
    source: dict[str, dict[str, float | int]],
) -> None:
    for key, row in source.items():
        merged = target.setdefault(key, _new_counter_row())
        merged["evaluated"] += int(row.get("evaluated", 0))
        merged["passed"] += int(row.get("passed", 0))


def audit_rule_funnel(
    db_path: Path,
    *,
    sessions: int = 10,
    source: str = "BREEZE",
) -> dict[str, Any]:
    if sessions < 1:
        raise ValueError("sessions must be at least 1")

    config = StrategyTunablesConfig()
    conn = _open_read_only(db_path)
    try:
        dates = _latest_spot_session_dates(conn, sessions=sessions, source=source)
        session_reports = [
            _audit_session(conn, day, source=source, config=config)
            for day in dates
        ]
    finally:
        conn.close()

    aggregate_gates: dict[str, dict[str, int]] = {}
    aggregate_components: dict[str, dict[str, int]] = {}
    aggregate_blockers: Counter[str] = Counter()
    aggregate_quality: Counter[str] = Counter()
    aggregate_reasons: dict[str, Counter[str]] = {
        "trend": Counter(),
        "confirmation": Counter(),
        "confluence": Counter(),
        "risk": Counter(),
    }
    aggregate_ready_direction: Counter[str] = Counter()
    progressive_totals = Counter({
        "directional_evaluations": 0,
        "trend_pass": 0,
        "trend_and_confirmation_pass": 0,
        "trend_confirmation_confluence_pass": 0,
        "risk_ready": 0,
    })
    completed_bars = 0

    for session_report in session_reports:
        completed_bars += int(session_report["completed_15m_decision_bars"])
        aggregate_blockers.update(session_report["blocker_counts"])
        aggregate_quality.update(session_report["data_quality_counts"])
        aggregate_ready_direction.update(session_report.get("ready_by_direction") or {})
        _merge_rows(aggregate_gates, session_report["gate_funnel"])
        _merge_rows(aggregate_components, session_report["component_funnel"])
        for gate_id, counts in session_report["gate_reason_counts"].items():
            aggregate_reasons[gate_id].update(counts)
        progressive = session_report["progressive_funnel"]
        for key in progressive_totals:
            progressive_totals[key] += int(progressive.get(key, 0))

    total = progressive_totals["directional_evaluations"]
    aggregate_progressive = {
        "directional_evaluations": total,
        "trend_pass": progressive_totals["trend_pass"],
        "trend_pass_pct": round(progressive_totals["trend_pass"] / total * 100, 2) if total else 0.0,
        "trend_and_confirmation_pass": progressive_totals["trend_and_confirmation_pass"],
        "trend_and_confirmation_pass_pct": round(
            progressive_totals["trend_and_confirmation_pass"] / total * 100, 2
        ) if total else 0.0,
        "trend_confirmation_confluence_pass": progressive_totals["trend_confirmation_confluence_pass"],
        "trend_confirmation_confluence_pass_pct": round(
            progressive_totals["trend_confirmation_confluence_pass"] / total * 100, 2
        ) if total else 0.0,
        "risk_ready": progressive_totals["risk_ready"],
        "risk_ready_pct": round(progressive_totals["risk_ready"] / total * 100, 2) if total else 0.0,
    }

    return {
        "audit_type": "STRATEGY_A_V3_RULE_FUNNEL_READ_ONLY",
        "db_path": str(db_path.resolve()),
        "source": source.upper(),
        "sessions_requested": sessions,
        "sessions_found": len(session_reports),
        "completed_15m_decision_bars": completed_bars,
        "directional_evaluations": total,
        "note": (
            "Each completed 15m bar is evaluated for CALL and PUT, so "
            "directional_evaluations should be 2x completed_15m_decision_bars."
        ),
        "thresholds": {
            "momentum_adx_min_delta_2bars": config.momentum_adx_min_delta_2bars,
            "momentum_ema20_slope_min_atr": config.momentum_ema20_slope_min_atr,
            "momentum_ema20_slope_max_atr": config.momentum_ema20_slope_max_atr,
            "ema_separation_min_atr": config.ema_separation_min_atr,
            "confirmation_min_body_ratio": config.confirmation_min_body_ratio,
            "confirmation_close_location_pct": config.confirmation_close_location_pct,
            "confirmation_max_range_atr": config.confirmation_max_range_atr,
            "confluence_distance_atr": config.confluence_distance_atr,
            "sr_zone_atr": config.sr_zone_atr,
            "minimum_stop_distance_atr": config.minimum_stop_distance_atr,
            "maximum_stop_distance_atr": config.maximum_stop_distance_atr,
            "minimum_room_to_opposing_sr_r": config.minimum_room_to_opposing_sr_r,
        },
        "aggregate": {
            "progressive_funnel": aggregate_progressive,
            "gate_funnel": _finalize_counter_rows(aggregate_gates),
            "component_funnel": _finalize_counter_rows(aggregate_components),
            "blocker_counts": dict(aggregate_blockers.most_common()),
            "data_quality_counts": dict(aggregate_quality.most_common()),
            "gate_reason_counts": {
                gate_id: dict(counter.most_common())
                for gate_id, counter in aggregate_reasons.items()
            },
            "ready_by_direction": dict(sorted(aggregate_ready_direction.items())),
        },
        "sessions": session_reports,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only Strategy A V2 rule-funnel audit over cached historical data"
    )
    parser.add_argument("--db-path", type=Path, default=_default_db_path())
    parser.add_argument("--sessions", type=int, default=10)
    parser.add_argument(
        "--source",
        choices=("BREEZE", "KITE", "LIVE", "MIXED"),
        default="BREEZE",
    )
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    report = audit_rule_funnel(
        args.db_path,
        sessions=args.sessions,
        source=args.source,
    )
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
