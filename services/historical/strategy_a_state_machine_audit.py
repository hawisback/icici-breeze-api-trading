"""Read-only Strategy A V2 state-machine/trigger audit.

Replays the production Strategy A state machine against the cached SQLite data
without broker calls, data writes, threshold changes, option selection, or
trade persistence. It records setup/armed/trigger/expiry/invalidation events.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from services.historical.strategy_a_data_audit import (
    ENTRY_FIRST_END,
    ENTRY_LAST_END,
    IST,
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


IMPORTANT_EVENTS = {
    "SETUP_CREATED",
    "TRIGGERED",
    "EXPIRED",
    "INVALIDATED",
    "REJECTED",
    "FORCED_EXIT",
    "ROLLOVER_RESET",
    "ENTERED",
    "ACTIVE_POSITION",
}


def _minutes(value: datetime) -> int:
    local = value.astimezone(IST)
    return local.hour * 60 + local.minute


def _in_entry_window(value: datetime, config: StrategyTunablesConfig) -> bool:
    start_h, start_m = map(int, config.entry_session_start.split(":"))
    end_h, end_m = map(int, config.entry_session_end.split(":"))
    minute = _minutes(value)
    return start_h * 60 + start_m <= minute <= end_h * 60 + end_m


def _setup_payload(strategy: TrendPullbackStrategy) -> dict[str, Any] | None:
    setup = strategy.snapshot.setup
    if setup is None:
        return None
    return {
        "direction": setup.direction.value,
        "setup_timestamp": setup.setup_timestamp.isoformat(),
        "confirmation_bar_timestamp": setup.confirmation_bar_timestamp.isoformat(),
        "trigger_price": setup.trigger_price,
        "structural_stop": setup.structural_stop,
        "initial_underlying_r": setup.initial_underlying_r,
        "relevant_support_resistance_level": setup.relevant_support_resistance_level,
        "confluence_references": list(setup.confluence_references),
        "setup_expiry_timestamp": setup.setup_expiry_timestamp.isoformat(),
        "setup_expiry_bar_index": setup.setup_expiry_bar_index,
    }


def _audit_session(conn: Any, day: Any, *, source: str, config: StrategyTunablesConfig) -> dict[str, Any]:
    session_start_utc, session_end_utc = _session_bounds(day)
    spot = _load_rows(
        conn,
        start_utc=session_start_utc,
        end_utc=session_end_utc,
        source=source,
        instrument_id="INST-NIFTY-INDEX",
    )
    all_futures = _load_rows(
        conn,
        start_utc=session_start_utc,
        end_utc=session_end_utc,
        source=source,
        instrument_like="INST-NIFTY-FUT-%",
    )
    active_contract = _active_contract(day, all_futures)
    if not active_contract:
        return {
            "date": day.isoformat(),
            "active_futures_contract": None,
            "event_counts": {},
            "events": [],
            "signals": [],
            "setup_count": 0,
            "signal_count": 0,
            "final_state": "FLAT",
            "skip_reason": "NO_ACTIVE_FUTURES_CONTRACT",
        }

    warmup_start = datetime.combine(
        day - timedelta(days=WARMUP_CALENDAR_DAYS),
        SESSION_START,
        tzinfo=IST,
    )
    futures_history = _load_rows(
        conn,
        start_utc=warmup_start.astimezone(session_start_utc.tzinfo),
        end_utc=session_end_utc,
        source=source,
        instrument_like="INST-NIFTY-FUT-%",
    )

    strategy = TrendPullbackStrategy(config=config, allow_session_bypass=False)
    event_counts: Counter[str] = Counter()
    events: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    seen_events: set[tuple[str, str, str | None]] = set()

    for bar in spot:
        as_of = bar.end_time
        if not _in_entry_window(as_of, config):
            continue
        futures = [candle for candle in futures_history if candle.end_time <= as_of]
        if not futures:
            continue

        signal = strategy.evaluate(
            SimpleNamespace(timestamp=as_of),
            [],
            [],
            futures_candles=futures,
            overrides=None,
        )
        event = strategy.last_event
        if event is not None and event.event != "DUPLICATE_IGNORED":
            key = (event.event, event.timestamp.isoformat(), event.reason)
            if key not in seen_events:
                seen_events.add(key)
                if event.event in IMPORTANT_EVENTS:
                    event_counts[event.event] += 1
                    events.append({
                        "event": event.event,
                        "timestamp": event.timestamp.isoformat(),
                        "timestamp_ist": event.timestamp.astimezone(IST).isoformat(),
                        "reason": event.reason,
                        "details": event.details,
                        "state_after": strategy.snapshot.state.value,
                        "setup_after": _setup_payload(strategy),
                    })

        if signal is not None:
            snapshot = signal.features_snapshot or {}
            signals.append({
                "signal_id": signal.signal_id,
                "timestamp": signal.timestamp.isoformat(),
                "timestamp_ist": signal.timestamp.astimezone(IST).isoformat(),
                "direction": signal.direction.value,
                "option_type": signal.option_type.value,
                "underlying_entry_price": signal.underlying_entry_price,
                "structural_stop": signal.structural_stop,
                "r_points": signal.r_points,
                "trigger_price": snapshot.get("trigger"),
                "atr14": snapshot.get("atr14"),
                "futures_contract": snapshot.get("futures_contract"),
                "completed_candle_timestamp": snapshot.get("completed_candle_timestamp"),
            })
            # Mirror replay behavior after a Strategy A signal is persisted:
            # confirm ENTERED so the same session cannot manufacture another
            # setup while the first position is considered active.
            strategy.confirm_entry(signal.timestamp)

    return {
        "date": day.isoformat(),
        "active_futures_contract": active_contract,
        "event_counts": dict(event_counts),
        "events": events,
        "signals": signals,
        "setup_count": int(event_counts.get("SETUP_CREATED", 0)),
        "signal_count": len(signals),
        "final_state": strategy.snapshot.state.value,
    }


def audit_state_machine(
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

    aggregate_events: Counter[str] = Counter()
    total_signals = 0
    setup_dates: list[str] = []
    signal_dates: list[str] = []
    for session in session_reports:
        aggregate_events.update(session["event_counts"])
        setup_count = int(session.get("setup_count", 0))
        signal_count = int(session.get("signal_count", 0))
        if setup_count:
            setup_dates.append(session["date"])
        if signal_count:
            signal_dates.append(session["date"])
            total_signals += signal_count

    return {
        "audit_type": "STRATEGY_A_V3_STATE_MACHINE_READ_ONLY",
        "db_path": str(db_path.resolve()),
        "source": source.upper(),
        "sessions_requested": sessions,
        "sessions_found": len(session_reports),
        "thresholds_unchanged": True,
        "entry_window": {
            "start": config.entry_session_start,
            "end": config.entry_session_end,
            "trigger_validity_bars": config.trigger_validity_bars,
            "maximum_chase_atr": config.maximum_chase_atr,
        },
        "aggregate": {
            "event_counts": dict(aggregate_events),
            "setup_count": sum(int(session.get("setup_count", 0)) for session in session_reports),
            "signal_count": total_signals,
            "setup_dates": setup_dates,
            "signal_dates": signal_dates,
        },
        "sessions": session_reports,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only Strategy A V3 state-machine/trigger audit"
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
    report = audit_state_machine(
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
