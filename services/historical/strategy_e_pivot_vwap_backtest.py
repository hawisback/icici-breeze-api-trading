"""Read-only bulk historical backtest for Strategy E Pivot/VWAP Scalp.

Reuses the production completed-5m signal evaluator and lifecycle helper.
Requires complete current/prior active-contract futures sessions. Native 1m
futures are used only to resolve same-5m stop/target ordering. No broker calls
or database writes are made.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Sequence

from libs.contracts.models import Candle
from services.historical.strategy_a_data_audit import (
    IST, _active_contract, _aware, _default_db_path, _open_read_only,
    _row_to_candle, _source_predicate,
)
from services.strategy.models import StrategyTunablesConfig, TradeDirection
from services.strategy.replay_intrabar import resolve_stop_target_order
from services.strategy.strategies.pivot_vwap_scalp import (
    PivotVwapScalpStrategy, StrategyEDecision,
    evaluate_strategy_e_lifecycle_bar,
)

REAL_SOURCES = {"BREEZE", "KITE", "LIVE"}
SESSION_START = time(9, 15)
SESSION_END_EXCLUSIVE = time(15, 30)
EXPECTED_5M_BARS = 75
DEFAULT_WARMUP_CALENDAR_DAYS = 14


def _session_date(candle: Candle) -> date:
    return candle.start_time.astimezone(IST).date()


def _dedupe_bars(candles: Sequence[Candle], *, interval: str) -> list[Candle]:
    selected: dict[tuple[str, datetime], Candle] = {}
    for candle in sorted(
        candles,
        key=lambda item: (item.start_time, item.source, item.instrument_id),
    ):
        if candle.interval != interval or candle.source not in REAL_SOURCES:
            continue
        selected.setdefault((candle.instrument_id, candle.start_time), candle)
    return sorted(selected.values(), key=lambda item: item.start_time)


def _group_futures(
    candles: Sequence[Candle], *, interval: str = "5m"
) -> dict[date, dict[str, list[Candle]]]:
    result: dict[date, dict[str, list[Candle]]] = {}
    for candle in _dedupe_bars(candles, interval=interval):
        result.setdefault(_session_date(candle), {}).setdefault(
            candle.instrument_id, []
        ).append(candle)
    return result


def _regular_session_bars(candles: Sequence[Candle]) -> list[Candle]:
    return sorted(
        [
            candle for candle in candles
            if SESSION_START
            <= candle.start_time.astimezone(IST).time().replace(tzinfo=None)
            < SESSION_END_EXCLUSIVE
        ],
        key=lambda item: item.start_time,
    )


def _is_complete_session(candles: Sequence[Candle]) -> bool:
    rows = _regular_session_bars(candles)
    if len(rows) != EXPECTED_5M_BARS:
        return False
    actual = {
        candle.start_time.astimezone(IST).replace(second=0, microsecond=0)
        for candle in rows
    }
    if len(actual) != EXPECTED_5M_BARS:
        return False
    day = _session_date(rows[0])
    cursor = datetime.combine(day, SESSION_START, tzinfo=IST)
    end = datetime.combine(day, SESSION_END_EXCLUSIVE, tzinfo=IST)
    expected: set[datetime] = set()
    while cursor < end:
        expected.add(cursor)
        cursor += timedelta(minutes=5)
    return actual == expected


def _active_contract_for_day(
    day: date, grouped: dict[date, dict[str, list[Candle]]]
) -> str | None:
    bars = [
        bar
        for rows in grouped.get(day, {}).values()
        for bar in rows
    ]
    return _active_contract(day, bars)


def _complete_candidate_dates(candles: Sequence[Candle]) -> list[date]:
    grouped = _group_futures(candles)
    market_days = sorted(grouped)
    result: list[date] = []
    for index, day in enumerate(market_days):
        if index == 0:
            continue
        previous_day = market_days[index - 1]
        contract = _active_contract_for_day(day, grouped)
        if not contract:
            continue
        if (
            _is_complete_session(grouped[day].get(contract, []))
            and _is_complete_session(grouped[previous_day].get(contract, []))
        ):
            result.append(day)
    return result


def _select_requested_session_dates(
    available_dates: Sequence[date],
    *,
    sessions: int,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[date]:
    if sessions <= 0:
        raise ValueError("sessions must be greater than zero")
    eligible = [
        day for day in sorted(set(available_dates))
        if (start_date is None or day >= start_date)
        and (end_date is None or day <= end_date)
    ]
    if len(eligible) < sessions:
        raise ValueError(
            f"requested {sessions} usable sessions but only "
            f"{len(eligible)} complete Strategy E sessions are available"
        )
    return eligible[:sessions] if start_date is not None else eligible[-sessions:]


def _period_metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    resolved = [
        row for row in rows
        if row.get("lifecycle_status") == "RESOLVED"
        and row.get("realized_r") is not None
    ]
    values = [float(row["realized_r"]) for row in resolved]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    equity = peak = max_drawdown = 0.0
    losing_streak = max_losing_streak = 0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
        if value < 0:
            losing_streak += 1
            max_losing_streak = max(max_losing_streak, losing_streak)
        else:
            losing_streak = 0
    return {
        "trades": len(rows),
        "resolved_trades": len(resolved),
        "ambiguous_trades": sum(
            row.get("lifecycle_status") == "AMBIGUOUS" for row in rows
        ),
        "unresolved_trades": sum(
            row.get("lifecycle_status") == "UNRESOLVED" for row in rows
        ),
        "win_rate_pct": (
            round(100.0 * len(wins) / len(values), 2) if values else 0.0
        ),
        "mean_r": round(mean(values), 6) if values else None,
        "total_r": round(sum(values), 6),
        "profit_factor": (
            round(sum(wins) / abs(sum(losses)), 6)
            if losses and wins
            else (float("inf") if wins and not losses else None)
        ),
        "max_drawdown_r": round(max_drawdown, 6),
        "max_losing_streak": max_losing_streak,
        "mean_mfe_r": (
            round(mean(float(row["mfe_r"]) for row in resolved), 6)
            if resolved else None
        ),
        "mean_mae_r": (
            round(mean(float(row["mae_r"]) for row in resolved), 6)
            if resolved else None
        ),
    }


def _r_for(
    direction: TradeDirection, entry: float, exit_price: float, risk: float
) -> float:
    if direction == TradeDirection.BULLISH:
        return (exit_price - entry) / risk
    return (entry - exit_price) / risk


def _start_trade(signal: Any) -> dict[str, Any]:
    snapshot = dict(signal.features_snapshot)
    return {
        "signal": signal,
        "entry": float(signal.underlying_entry_price or signal.spot_reference_price),
        "stop": float(signal.structural_stop),
        "risk": float(signal.r_points),
        "target": float(snapshot["target_price"]),
        "mfe_points": 0.0,
        "mae_points": 0.0,
    }


def _advance_trade(
    state: dict[str, Any],
    bar: Candle,
    minute_bars: Sequence[Candle],
    *,
    forced_exit_time: str,
) -> dict[str, Any] | None:
    signal = state["signal"]
    if bar.end_time <= signal.timestamp:
        return None
    direction = signal.direction
    entry = float(state["entry"])
    stop = float(state["stop"])
    risk = float(state["risk"])
    target = float(state["target"])
    if direction == TradeDirection.BULLISH:
        state["mfe_points"] = max(
            float(state["mfe_points"]), float(bar.high) - entry
        )
        state["mae_points"] = min(
            float(state["mae_points"]), float(bar.low) - entry
        )
    else:
        state["mfe_points"] = max(
            float(state["mfe_points"]), entry - float(bar.low)
        )
        state["mae_points"] = min(
            float(state["mae_points"]), entry - float(bar.high)
        )

    decision = evaluate_strategy_e_lifecycle_bar(
        direction=direction,
        entry=entry,
        risk=risk,
        stop=stop,
        target=target,
        bar=bar,
        forced_exit_time=forced_exit_time,
    )
    event_time = bar.end_time
    if decision.stop_hit or decision.target_hit:
        minutes = [
            candle for candle in minute_bars
            if candle.instrument_id == bar.instrument_id
            and candle.start_time >= bar.start_time
            and candle.start_time < bar.end_time
        ]
        if decision.stop_hit and decision.target_hit and not minutes:
            return {
                "lifecycle_status": "AMBIGUOUS",
                "exit_time": bar.end_time,
                "exit_price": None,
                "exit_reason": "AMBIGUOUS_INTRABAR_ORDER",
                "realized_r": None,
                "mfe_r": float(state["mfe_points"]) / risk,
                "mae_r": float(state["mae_points"]) / risk,
                "intrabar_resolution": "1M_UNAVAILABLE",
            }
        if minutes:
            resolution = resolve_stop_target_order(
                direction,
                active_stop=stop,
                target=target,
                minute_candles=minutes,
            )
            if resolution.ambiguous:
                return {
                    "lifecycle_status": "AMBIGUOUS",
                    "exit_time": resolution.event_time or bar.end_time,
                    "exit_price": None,
                    "exit_reason": "AMBIGUOUS_INTRABAR_ORDER",
                    "realized_r": None,
                    "mfe_r": float(state["mfe_points"]) / risk,
                    "mae_r": float(state["mae_points"]) / risk,
                    "intrabar_resolution": "STILL_AMBIGUOUS_1M",
                }
            if resolution.event == "STRUCTURAL_STOP":
                decision = decision.__class__(
                    exit_reason="STRATEGY_E_STOP_LOSS",
                    decision_price=float(resolution.exit_price),
                    current_r=decision.current_r,
                    stop_hit=True,
                    target_hit=decision.target_hit,
                    force_exit=False,
                )
                event_time = resolution.event_time or bar.end_time
            elif resolution.event == "TARGET":
                decision = decision.__class__(
                    exit_reason="STRATEGY_E_TARGET",
                    decision_price=float(resolution.exit_price),
                    current_r=decision.current_r,
                    stop_hit=decision.stop_hit,
                    target_hit=True,
                    force_exit=False,
                )
                event_time = resolution.event_time or bar.end_time

    if decision.exit_reason is None:
        return None
    return {
        "lifecycle_status": "RESOLVED",
        "exit_time": event_time,
        "exit_price": float(decision.decision_price),
        "exit_reason": decision.exit_reason,
        "realized_r": round(
            _r_for(direction, entry, float(decision.decision_price), risk), 6
        ),
        "mfe_r": round(float(state["mfe_points"]) / risk, 6),
        "mae_r": round(float(state["mae_points"]) / risk, 6),
        "intrabar_resolution": (
            "1M_USED"
            if (decision.stop_hit or decision.target_hit) and any(
                candle.instrument_id == bar.instrument_id
                and candle.start_time >= bar.start_time
                and candle.start_time < bar.end_time
                for candle in minute_bars
            )
            else "5M_OR_FORCED_EXIT"
        ),
    }


def _trade_row(
    day: date, state: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    signal = state["signal"]
    snapshot = dict(signal.features_snapshot)
    return {
        "date": day.isoformat(),
        "signal_id": signal.signal_id,
        "signal_type": snapshot.get("signal_type"),
        "direction": signal.direction.value,
        "option_type": (
            signal.option_type.value
            if hasattr(signal.option_type, "value")
            else str(signal.option_type)
        ),
        "futures_contract": snapshot.get("futures_contract"),
        "entry_time": signal.timestamp.isoformat(),
        "entry_price": float(state["entry"]),
        "stop": float(state["stop"]),
        "target": float(state["target"]),
        "risk_points": float(state["risk"]),
        "pivot": snapshot.get("pivot"),
        "vwap": snapshot.get("vwap"),
        "relative_volume": snapshot.get("relative_volume"),
        "volume_confirmed": snapshot.get("volume_confirmed"),
        "choppy": snapshot.get("choppy"),
        **{
            key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in result.items()
        },
    }


def _config_snapshot(config: StrategyTunablesConfig) -> dict[str, Any]:
    fields = [
        "strategy_e_countertrend_enabled",
        "strategy_e_swing_lookback",
        "strategy_e_volume_lookback",
        "strategy_e_rvol_confirmation",
        "strategy_e_sr_lookback_bars",
        "strategy_e_sr_buffer_points",
        "strategy_e_counter_zone_points",
        "strategy_e_stop_buffer_points",
        "strategy_e_max_stop_points",
        "strategy_e_trend_target_points",
        "strategy_e_counter_target_points",
        "strategy_e_min_reward_risk",
        "strategy_e_min_room_to_level_points",
        "strategy_e_chop_lookback_bars",
        "strategy_e_chop_cross_threshold",
        "strategy_e_flat_vwap_lookback_bars",
        "strategy_e_flat_vwap_threshold_points",
        "strategy_e_entry_start",
        "strategy_e_entry_end",
        "strategy_e_forced_exit_time",
    ]
    return {field: getattr(config, field) for field in fields}


def run_backtest(
    *,
    futures_candles: Sequence[Candle],
    one_minute_futures_candles: Sequence[Candle] = (),
    start_date: date | None = None,
    end_date: date | None = None,
    session_dates: Sequence[date] | None = None,
    config: StrategyTunablesConfig | None = None,
) -> dict[str, Any]:
    cfg = config or StrategyTunablesConfig()
    grouped = _group_futures(futures_candles)
    grouped_1m = _group_futures(one_minute_futures_candles, interval="1m")
    market_days = sorted(grouped)
    requested = set(session_dates) if session_dates is not None else None
    trades: list[dict[str, Any]] = []
    usable_dates: list[date] = []
    skipped: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    result_counts: Counter[str] = Counter()
    signal_type_counts: Counter[str] = Counter()
    per_session: dict[str, dict[str, Any]] = {}
    qualified_signals = blocked_open_position = bars_evaluated = 0

    for index, day in enumerate(market_days):
        if start_date is not None and day < start_date:
            continue
        if end_date is not None and day > end_date:
            continue
        if requested is not None and day not in requested:
            continue
        if index == 0:
            skipped["PREVIOUS_TRADING_SESSION_UNAVAILABLE"] += 1
            continue
        previous_day = market_days[index - 1]
        contract = _active_contract_for_day(day, grouped)
        if not contract:
            skipped["ACTIVE_FUTURES_CONTRACT_UNAVAILABLE"] += 1
            continue
        current = _regular_session_bars(grouped[day].get(contract, []))
        previous = _regular_session_bars(
            grouped[previous_day].get(contract, [])
        )
        if not _is_complete_session(current):
            skipped["INCOMPLETE_CURRENT_FUTURES_5M"] += 1
            continue
        if not _is_complete_session(previous):
            skipped["INCOMPLETE_PREVIOUS_FUTURES_5M"] += 1
            continue

        usable_dates.append(day)
        strategy = PivotVwapScalpStrategy(cfg)
        minute_rows = grouped_1m.get(day, {}).get(contract, [])
        history = list(previous)
        active: dict[str, Any] | None = None
        day_reasons: Counter[str] = Counter()
        day_results: Counter[str] = Counter()
        day_signal_types: Counter[str] = Counter()
        day_trade_start = len(trades)
        day_qualified = day_blocked = 0

        for bar in current:
            if active is not None:
                lifecycle = _advance_trade(
                    active, bar, minute_rows,
                    forced_exit_time=cfg.strategy_e_forced_exit_time,
                )
                if lifecycle is not None:
                    trades.append(_trade_row(day, active, lifecycle))
                    active = None

            history.append(bar)
            decision: StrategyEDecision = strategy.evaluate(
                history,
                as_of=bar.end_time,
                expected_completed_end=bar.end_time,
            )
            bars_evaluated += 1
            reason_counts[decision.reason] += 1
            result_counts[decision.result] += 1
            day_reasons[decision.reason] += 1
            day_results[decision.result] += 1
            if decision.signal is None:
                continue

            qualified_signals += 1
            day_qualified += 1
            signal_type = str(
                decision.signal.features_snapshot.get("signal_type")
                or decision.result
            )
            signal_type_counts[signal_type] += 1
            day_signal_types[signal_type] += 1
            if active is not None:
                blocked_open_position += 1
                day_blocked += 1
                continue
            active = _start_trade(decision.signal)

        if active is not None:
            trades.append(_trade_row(
                day,
                active,
                {
                    "lifecycle_status": "UNRESOLVED",
                    "exit_time": None,
                    "exit_price": None,
                    "exit_reason": "SESSION_ENDED_WITH_OPEN_POSITION",
                    "realized_r": None,
                    "mfe_r": round(
                        float(active["mfe_points"]) / float(active["risk"]), 6
                    ),
                    "mae_r": round(
                        float(active["mae_points"]) / float(active["risk"]), 6
                    ),
                    "intrabar_resolution": "NONE",
                },
            ))

        day_rows = trades[day_trade_start:]
        per_session[day.isoformat()] = {
            "active_futures_contract": contract,
            "bars_evaluated": len(current),
            "one_minute_candles": len(minute_rows),
            "qualified_signals": day_qualified,
            "signals_blocked_position_open": day_blocked,
            "executed_trades": len(day_rows),
            "resolved_trades": sum(
                row["lifecycle_status"] == "RESOLVED" for row in day_rows
            ),
            "ambiguous_trades": sum(
                row["lifecycle_status"] == "AMBIGUOUS" for row in day_rows
            ),
            "decision_reason_counts": dict(day_reasons),
            "result_counts": dict(day_results),
            "signal_type_counts": dict(day_signal_types),
        }

    if requested is not None:
        missing = requested - set(usable_dates)
        if missing:
            skipped["REQUESTED_SESSION_NOT_USABLE"] += len(missing)

    metrics = _period_metrics(trades)
    metrics.update({
        "usable_sessions": len(usable_dates),
        "trades_per_session": (
            round(len(trades) / len(usable_dates), 6) if usable_dates else 0.0
        ),
        "direction_counts": dict(Counter(row["direction"] for row in trades)),
        "signal_type_counts": dict(
            Counter(str(row.get("signal_type")) for row in trades)
        ),
        "exit_reason_counts": dict(
            Counter(str(row.get("exit_reason")) for row in trades)
        ),
    })
    trade_dates = {row["date"] for row in trades}
    return {
        "strategy_id": "STRATEGY_E_PIVOT_VWAP_SCALP_PRODUCTION",
        "signal_authority": "PivotVwapScalpStrategy.evaluate",
        "lifecycle_authority": "evaluate_strategy_e_lifecycle_bar",
        "config": _config_snapshot(cfg),
        "usable_sessions": len(usable_dates),
        "session_summary": {
            "session_count": len(usable_dates),
            "session_dates": [day.isoformat() for day in usable_dates],
            "trade_days": sum(
                day.isoformat() in trade_dates for day in usable_dates
            ),
            "no_trade_days": sum(
                day.isoformat() not in trade_dates for day in usable_dates
            ),
            "trades_by_date": {
                day.isoformat(): sum(
                    row["date"] == day.isoformat() for row in trades
                )
                for day in usable_dates
            },
        },
        "metrics": metrics,
        "signal_diagnostics": {
            "bars_evaluated": bars_evaluated,
            "qualified_signals": qualified_signals,
            "signals_blocked_position_open": blocked_open_position,
            "decision_reason_counts": dict(reason_counts),
            "result_counts": dict(result_counts),
            "signal_type_counts": dict(signal_type_counts),
            "per_session": per_session,
        },
        "intrabar_coverage": {
            "one_minute_candles_loaded": len(
                _dedupe_bars(one_minute_futures_candles, interval="1m")
            ),
            "usable_sessions_with_any_1m": sum(
                bool(per_session[day.isoformat()]["one_minute_candles"])
                for day in usable_dates
            ),
            "usable_sessions_without_1m": sum(
                not bool(per_session[day.isoformat()]["one_minute_candles"])
                for day in usable_dates
            ),
            "ambiguous_trades": metrics["ambiguous_trades"],
        },
        "skipped_sessions_or_events": dict(skipped),
        "trades": trades,
    }


def _load_futures_interval_rows(
    conn: Any,
    *,
    interval: str,
    start_utc: datetime,
    end_utc: datetime,
    source: str,
) -> list[Candle]:
    source_clause, source_params = _source_predicate(source)
    params: list[Any] = [
        interval, start_utc.isoformat(), end_utc.isoformat(), *source_params
    ]
    rows = conn.execute(
        f"""
        SELECT instrument_id, interval, start_time, end_time,
               open, high, low, close, volume, open_interest, source
        FROM historical_candles
        WHERE instrument_id LIKE 'INST-NIFTY-FUT-%'
          AND interval = ?
          AND start_time >= ?
          AND start_time < ?
          AND {source_clause}
        ORDER BY start_time ASC, instrument_id ASC, source ASC
        """,
        params,
    ).fetchall()
    return [_row_to_candle(row) for row in rows]


def _available_complete_session_dates(conn: Any, source: str) -> list[date]:
    source_clause, source_params = _source_predicate(source)
    bounds = conn.execute(
        f"""
        SELECT MIN(start_time) AS first_ts, MAX(start_time) AS last_ts
        FROM historical_candles
        WHERE instrument_id LIKE 'INST-NIFTY-FUT-%'
          AND interval = '5m'
          AND {source_clause}
        """,
        source_params,
    ).fetchone()
    if not bounds or not bounds["first_ts"] or not bounds["last_ts"]:
        raise ValueError(
            f"no NIFTY futures 5m candles available for source={source}"
        )
    first = _aware(bounds["first_ts"]).astimezone(IST).date()
    last = _aware(bounds["last_ts"]).astimezone(IST).date()
    start_utc = datetime.combine(
        first, SESSION_START, tzinfo=IST
    ).astimezone(timezone.utc)
    end_utc = (
        datetime.combine(last, SESSION_END_EXCLUSIVE, tzinfo=IST)
        + timedelta(days=1)
    ).astimezone(timezone.utc)
    return _complete_candidate_dates(_load_futures_interval_rows(
        conn,
        interval="5m",
        start_utc=start_utc,
        end_utc=end_utc,
        source=source,
    ))


def load_historical_candles(
    *,
    db_path: Path,
    source: str,
    start_date: date,
    end_date: date,
) -> tuple[list[Candle], list[Candle]]:
    warmup_start = start_date - timedelta(days=DEFAULT_WARMUP_CALENDAR_DAYS)
    start_utc = datetime.combine(
        warmup_start, SESSION_START, tzinfo=IST
    ).astimezone(timezone.utc)
    end_utc = (
        datetime.combine(end_date, SESSION_END_EXCLUSIVE, tzinfo=IST)
        + timedelta(days=1)
    ).astimezone(timezone.utc)
    with _open_read_only(db_path) as conn:
        futures = _load_futures_interval_rows(
            conn, interval="5m", start_utc=start_utc,
            end_utc=end_utc, source=source,
        )
        one_minute = _load_futures_interval_rows(
            conn, interval="1m", start_utc=start_utc,
            end_utc=end_utc, source=source,
        )
    return futures, one_minute


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Strategy E Pivot/VWAP read-only bulk backtest"
    )
    parser.add_argument("--db", type=Path, default=_default_db_path())
    parser.add_argument(
        "--source", default="BREEZE",
        choices=("BREEZE", "KITE", "LIVE", "MIXED"),
    )
    parser.add_argument("--sessions", type=int)
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument(
        "--output", type=Path,
        default=Path("data") / "strategy_e_pivot_vwap_backtest.json",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    with _open_read_only(args.db) as conn:
        available = _available_complete_session_dates(conn, args.source)
    if args.sessions is not None:
        try:
            selected_dates = _select_requested_session_dates(
                available,
                sessions=args.sessions,
                start_date=args.start_date,
                end_date=args.end_date,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    else:
        selected_dates = [
            day for day in available
            if (args.start_date is None or day >= args.start_date)
            and (args.end_date is None or day <= args.end_date)
        ]
        if not selected_dates:
            raise SystemExit(
                "no complete Strategy E sessions match the requested range"
            )
    start_date, end_date = selected_dates[0], selected_dates[-1]
    futures, one_minute = load_historical_candles(
        db_path=args.db,
        source=args.source,
        start_date=start_date,
        end_date=end_date,
    )
    report = run_backtest(
        futures_candles=futures,
        one_minute_futures_candles=one_minute,
        start_date=start_date,
        end_date=end_date,
        session_dates=selected_dates,
    )
    if args.sessions is not None and report["usable_sessions"] != args.sessions:
        raise SystemExit(
            f"requested {args.sessions} complete Strategy E sessions but replay "
            f"produced {report['usable_sessions']}; review skipped/data coverage"
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(
        {
            "strategy_id": report["strategy_id"],
            "metrics": report["metrics"],
            "session_summary": report["session_summary"],
            "signal_diagnostics": report["signal_diagnostics"],
            "intrabar_coverage": report["intrabar_coverage"],
            "skipped": report["skipped_sessions_or_events"],
            "output": str(args.output),
        },
        indent=2,
        sort_keys=True,
    ))


if __name__ == "__main__":
    main()
