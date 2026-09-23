"""Read-only Breeze/Kite historical backtest for Strategy D V1.

The report is deliberately isolated from production strategy state. It reads
completed 5m NIFTY spot and active-futures candles from historical.db, builds
previous-session classic pivots, evaluates Strategy D without look-ahead, and
replays the ATR/T1/breakeven/EMA9/R2-S2 lifecycle in underlying R.

No broker calls are made, no database rows are written, and no Strategy A/B/C
configuration or runtime state is imported or mutated.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Sequence

from libs.contracts.models import Candle
from services.historical.strategy_a_data_audit import (
    IST,
    _active_contract,
    _aware,
    _default_db_path,
    _load_rows,
    _open_read_only,
    _row_to_candle,
    _source_predicate,
)
from services.strategy.strategies.sr_momentum_breakout import (
    REAL_SOURCES,
    StrategyDConfig,
    StrategyDPositionManager,
    evaluate_strategy_d_signal,
    previous_session_levels,
)


SESSION_START = time(9, 15)
SESSION_END_EXCLUSIVE = time(15, 35)
DEFAULT_WARMUP_CALENDAR_DAYS = 35


def _session_date(candle: Candle) -> date:
    return candle.start_time.astimezone(IST).date()


def _group_by_day(
    candles: Sequence[Candle],
    *,
    interval: str = "5m",
) -> dict[date, list[Candle]]:
    result: dict[date, list[Candle]] = {}
    for candle in candles:
        if candle.interval != interval or candle.source not in REAL_SOURCES:
            continue
        result.setdefault(_session_date(candle), []).append(candle)
    for day in result:
        result[day].sort(key=lambda item: item.start_time)
    return result


def _active_futures_by_day(candles: Sequence[Candle]) -> dict[date, list[Candle]]:
    grouped = _group_by_day(candles, interval="5m")
    result: dict[date, list[Candle]] = {}
    for day, bars in grouped.items():
        contract = _active_contract(day, bars)
        if contract:
            result[day] = [bar for bar in bars if bar.instrument_id == contract]
    return result


def _period_metrics(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    values = [float(row["realized_r"]) for row in rows]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    return {
        "trades": len(values),
        "win_rate_pct": (
            round(100.0 * len(wins) / len(values), 2)
            if values
            else 0.0
        ),
        "mean_r": round(mean(values), 6) if values else None,
        "total_r": round(sum(values), 6),
        "profit_factor": (
            round(sum(wins) / abs(sum(losses)), 6)
            if losses and sum(losses) != 0
            else None
        ),
    }


def _metrics(
    trades: Sequence[dict[str, Any]],
    usable_sessions: int,
) -> dict[str, Any]:
    values = [float(row["realized_r"]) for row in trades]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    max_losing_streak = 0
    losing_streak = 0
    by_year: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_hour: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_level: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_direction: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in trades:
        value = float(row["realized_r"])
        equity += value
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity - peak)
        if value < 0:
            losing_streak += 1
            max_losing_streak = max(max_losing_streak, losing_streak)
        else:
            losing_streak = 0
        by_year[str(row["date"])[:4]].append(row)
        local = datetime.fromisoformat(
            str(row["entry_time"]).replace("Z", "+00:00")
        ).astimezone(IST)
        by_hour[f"{local.hour:02d}:00"].append(row)
        by_level[str(row["breakout_level_name"])].append(row)
        by_direction[str(row["direction"])].append(row)

    return {
        "trades": len(values),
        "usable_sessions": usable_sessions,
        "trades_per_session": (
            round(len(values) / usable_sessions, 6)
            if usable_sessions
            else 0.0
        ),
        "win_rate_pct": (
            round(100.0 * len(wins) / len(values), 2)
            if values
            else 0.0
        ),
        "mean_r": round(mean(values), 6) if values else None,
        "total_r": round(sum(values), 6),
        "profit_factor": (
            round(sum(wins) / abs(sum(losses)), 6)
            if losses and sum(losses) != 0
            else None
        ),
        "max_drawdown_r": round(max_drawdown, 6),
        "max_losing_streak": max_losing_streak,
        "scale_out_trades": sum(
            row.get("scale_out_time") is not None for row in trades
        ),
        "direction_counts": dict(
            sorted(Counter(row["direction"] for row in trades).items())
        ),
        "breakout_level_counts": dict(
            sorted(
                Counter(
                    row["breakout_level_name"] for row in trades
                ).items()
            )
        ),
        "exit_reason_counts": dict(
            sorted(
                Counter(
                    row["runner_exit_reason"] for row in trades
                ).items()
            )
        ),
        "segments": {
            "year": {
                key: _period_metrics(value)
                for key, value in sorted(by_year.items())
            },
            "entry_hour_ist": {
                key: _period_metrics(value)
                for key, value in sorted(by_hour.items())
            },
            "breakout_level": {
                key: _period_metrics(value)
                for key, value in sorted(by_level.items())
            },
            "direction": {
                key: _period_metrics(value)
                for key, value in sorted(by_direction.items())
            },
        },
    }


def run_backtest(
    *,
    spot_candles: Sequence[Candle],
    futures_candles: Sequence[Candle],
    one_minute_spot_candles: Sequence[Candle] = (),
    start_date: date | None = None,
    end_date: date | None = None,
    config: StrategyDConfig | None = None,
) -> dict[str, Any]:
    """Run one frozen Strategy D ruleset on preloaded real candles."""
    cfg = config or StrategyDConfig.v1_control()
    spot = sorted(spot_candles, key=lambda item: item.start_time)
    spot_by_day = _group_by_day(spot, interval="5m")
    minute_by_day = _group_by_day(
        one_minute_spot_candles,
        interval="1m",
    )
    futures_by_day = _active_futures_by_day(futures_candles)
    available_days = sorted(spot_by_day)
    if start_date is not None:
        available_days = [
            day for day in available_days if day >= start_date
        ]
    if end_date is not None:
        available_days = [
            day for day in available_days if day <= end_date
        ]

    manager = StrategyDPositionManager(strategy_d_config=cfg)
    trades: list[dict[str, Any]] = []
    levels_rows: list[dict[str, Any]] = []
    usable_sessions = 0
    usable_sessions_with_1m = 0
    skipped: Counter[str] = Counter()

    for day in available_days:
        day_spot = spot_by_day.get(day, [])
        day_minutes = minute_by_day.get(day, [])
        day_futures = futures_by_day.get(day, [])
        levels = previous_session_levels(spot, day)
        if levels is None:
            skipped["NO_PREVIOUS_SESSION_LEVELS"] += 1
            continue
        if not day_futures:
            skipped["NO_ACTIVE_FUTURES_5M"] += 1
            continue
        usable_sessions += 1
        if day_minutes:
            usable_sessions_with_1m += 1
        levels_rows.append(levels.to_dict())
        used_levels: set[tuple[str, str]] = set()
        index = 0
        while index < len(day_spot):
            bar = day_spot[index]
            history = [
                item for item in spot
                if item.end_time <= bar.end_time
            ]
            futures_history = [
                item for item in day_futures
                if item.end_time <= bar.end_time
            ]
            signal = evaluate_strategy_d_signal(
                history,
                futures_history,
                levels,
                cfg,
            )
            signal_key = (
                (signal.option_type, signal.breakout_level_name)
                if signal
                else None
            )
            if signal is None or signal_key in used_levels:
                index += 1
                continue

            future_bars = day_spot[index + 1 :]
            if not future_bars:
                skipped["SIGNAL_WITHOUT_POST_ENTRY_BAR"] += 1
                break
            lifecycle = manager.replay_underlying_lifecycle(
                signal,
                history_through_entry=history,
                future_bars=future_bars,
                one_minute_bars=day_minutes,
            )
            used_levels.add(signal_key)
            row = {
                "strategy_id": signal.strategy_id,
                "date": day.isoformat(),
                "source_level_date": (
                    levels.source_session_date.isoformat()
                ),
                "direction": signal.option_type,
                "breakout_level_name": signal.breakout_level_name,
                "breakout_level": signal.breakout_level,
                "entry_time": signal.timestamp.isoformat(),
                "entry_price": signal.entry_price,
                "atr_5m": signal.atr_5m,
                "initial_stop": signal.initial_stop,
                "risk_points": signal.risk_points,
                "rsi_previous": signal.rsi_previous,
                "rsi_current": signal.rsi_current,
                "rsi_clearance_points": signal.rsi_clearance_points,
                "previous_day_range_atr": (
                    signal.previous_day_range_atr
                ),
                "vwap_reference_price": signal.vwap_reference_price,
                "vwap": signal.vwap,
                "vwap_source": signal.vwap_source,
                "next_pivot_name": signal.next_pivot_name,
                "next_pivot_price": signal.next_pivot_price,
                **lifecycle.to_dict(),
            }
            trades.append(row)

            while (
                index < len(day_spot)
                and day_spot[index].end_time <= lifecycle.exit_time
            ):
                index += 1

    return {
        "research_type": (
            "STRATEGY_D_SR_MOMENTUM_BREAKOUT_BACKTEST"
        ),
        "strategy_id": cfg.strategy_id,
        "ruleset_version": cfg.ruleset_version,
        "variant": cfg.variant,
        "config": cfg.to_dict(),
        "data_contract": {
            "signal_price": "NIFTY spot completed 5m candles",
            "static_levels": (
                "previous NIFTY spot trading session PDH/PDL/PDC "
                "and classic pivots"
            ),
            "vwap_confirmation": (
                "active NIFTY futures completed 5m session VWAP"
            ),
            "intrabar_ordering": (
                "native NIFTY spot 1m children when all five minutes "
                "exist; otherwise conservative 5m fallback"
            ),
            "sources_allowed": sorted(REAL_SOURCES),
            "lookahead": False,
        },
        "execution_model": {
            "entry": (
                "completed 5m spot close after structural break + "
                "RSI cross/clearance + futures VWAP alignment + "
                "previous-day range regime"
            ),
            "initial_stop": (
                "1.5 x spot 5m ATR on the adverse side"
            ),
            "scale_out": (
                "idealized 50% at +1.5R for underlying research"
            ),
            "runner_stop_after_scale": "breakeven at entry",
            "runner_exit": (
                "completed 5m close across EMA9, R2/S2 if beyond "
                "+1.5R, or session force exit"
            ),
            "same_bar_ambiguity": (
                "resolved with complete native 1m children; same-minute "
                "ambiguity remains conservative; incomplete 1m falls "
                "back to 5m without retroactive breakeven activation"
            ),
            "option_pnl": "NOT_SYNTHESIZED",
            "lot_execution": (
                "runtime uses selected contract lot_size; "
                "PositionManager whole-lot sizing is reused"
            ),
        },
        "metrics": _metrics(trades, usable_sessions),
        "usable_sessions": usable_sessions,
        "intrabar_coverage": {
            "one_minute_candles_loaded": len(
                one_minute_spot_candles
            ),
            "usable_sessions_with_any_1m": (
                usable_sessions_with_1m
            ),
            "usable_sessions_without_1m": (
                usable_sessions - usable_sessions_with_1m
            ),
        },
        "skipped_sessions_or_events": dict(sorted(skipped.items())),
        "levels": levels_rows,
        "trades": trades,
        "limitations": [
            (
                "This report validates the signal and underlying "
                "lifecycle before option execution economics are added."
            ),
            (
                "NIFTY spot is used for structural breaks, RSI and ATR; "
                "active NIFTY futures is used only for its volume-backed "
                "VWAP alignment."
            ),
            (
                "The 50% scale-out is an underlying-R research "
                "assumption. Live/paper execution must use whole option "
                "lots; a one-lot position cannot be halved."
            ),
            (
                "Historical option bid/ask is not fabricated. Option "
                "contract selection, slippage and transaction costs "
                "belong to the paper-validation layer."
            ),
            (
                "V2 entry thresholds were selected after review of the "
                "V1 Breeze sample and are therefore in-sample research "
                "hypotheses until validated on later/paper evidence."
            ),
            (
                "R1 and the 12:00 IST hour remain diagnostics, not hard "
                "filters, because their weakness was less stable across "
                "the observed calendar splits."
            ),
            (
                "Strategy A, Strategy B and Strategy C are not modified "
                "or evaluated by this module."
            ),
        ],
        "production_thresholds_changed": False,
        "market_data_written": False,
        "broker_called": False,
    }


def build_v2_comparison(
    *,
    spot_candles: Sequence[Candle],
    futures_candles: Sequence[Candle],
    one_minute_spot_candles: Sequence[Candle] = (),
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    """Return V2 as the main report with corrected V1 metrics beside it."""
    control = run_backtest(
        spot_candles=spot_candles,
        futures_candles=futures_candles,
        one_minute_spot_candles=one_minute_spot_candles,
        start_date=start_date,
        end_date=end_date,
        config=StrategyDConfig.v1_control(),
    )
    candidate = run_backtest(
        spot_candles=spot_candles,
        futures_candles=futures_candles,
        one_minute_spot_candles=one_minute_spot_candles,
        start_date=start_date,
        end_date=end_date,
        config=StrategyDConfig.v2_candidate(),
    )
    control_metrics = control["metrics"]
    candidate_metrics = candidate["metrics"]
    candidate["comparison_to_corrected_v1"] = {
        "control_strategy_id": control["strategy_id"],
        "control_config": control["config"],
        "control_metrics": control_metrics,
        "delta": {
            "trades": (
                candidate_metrics["trades"]
                - control_metrics["trades"]
            ),
            "total_r": round(
                candidate_metrics["total_r"]
                - control_metrics["total_r"],
                6,
            ),
            "mean_r": (
                round(
                    candidate_metrics["mean_r"]
                    - control_metrics["mean_r"],
                    6,
                )
                if candidate_metrics["mean_r"] is not None
                and control_metrics["mean_r"] is not None
                else None
            ),
            "profit_factor": (
                round(
                    candidate_metrics["profit_factor"]
                    - control_metrics["profit_factor"],
                    6,
                )
                if candidate_metrics["profit_factor"] is not None
                and control_metrics["profit_factor"] is not None
                else None
            ),
            "max_drawdown_r": round(
                candidate_metrics["max_drawdown_r"]
                - control_metrics["max_drawdown_r"],
                6,
            ),
        },
    }
    return candidate


def _available_spot_dates(
    conn: Any,
    source: str,
) -> tuple[date, date]:
    source_clause, source_params = _source_predicate(source)
    rows = conn.execute(
        f"""
        SELECT MIN(start_time) AS first_ts, MAX(start_time) AS last_ts
        FROM historical_candles
        WHERE instrument_id = 'INST-NIFTY-INDEX'
          AND interval = '5m'
          AND {source_clause}
        """,
        source_params,
    ).fetchone()
    if not rows or not rows["first_ts"] or not rows["last_ts"]:
        raise ValueError(
            f"no NIFTY 5m spot candles available for source={source}"
        )
    first = _aware(rows["first_ts"]).astimezone(IST).date()
    last = _aware(rows["last_ts"]).astimezone(IST).date()
    return first, last


def _load_spot_interval_rows(
    conn: Any,
    *,
    interval: str,
    start_utc: datetime,
    end_utc: datetime,
    source: str,
) -> list[Candle]:
    clauses = [
        "instrument_id = 'INST-NIFTY-INDEX'",
        "interval = ?",
        "start_time >= ?",
        "start_time < ?",
    ]
    params: list[Any] = [
        interval,
        start_utc.isoformat(),
        end_utc.isoformat(),
    ]
    source_clause, source_params = _source_predicate(source)
    clauses.append(source_clause)
    params.extend(source_params)
    rows = conn.execute(
        f"""
        SELECT instrument_id, interval, start_time, end_time,
               open, high, low, close, volume, open_interest, source
        FROM historical_candles
        WHERE {' AND '.join(clauses)}
        ORDER BY start_time ASC
        """,
        params,
    ).fetchall()
    return [_row_to_candle(row) for row in rows]


def load_historical_candles(
    *,
    db_path: Path,
    source: str,
    start_date: date,
    end_date: date,
) -> tuple[list[Candle], list[Candle], list[Candle]]:
    """Read 5m signal data plus optional native 1m spot for ordering."""
    warmup_start = (
        start_date - timedelta(days=DEFAULT_WARMUP_CALENDAR_DAYS)
    )
    start_ist = datetime.combine(
        warmup_start,
        SESSION_START,
        tzinfo=IST,
    )
    end_ist = datetime.combine(
        end_date,
        SESSION_END_EXCLUSIVE,
        tzinfo=IST,
    )
    start_utc = start_ist.astimezone(timezone.utc)
    end_utc = end_ist.astimezone(timezone.utc)
    with _open_read_only(db_path) as conn:
        spot = _load_rows(
            conn,
            start_utc=start_utc,
            end_utc=end_utc,
            source=source,
            instrument_id="INST-NIFTY-INDEX",
        )
        futures = _load_rows(
            conn,
            start_utc=start_utc,
            end_utc=end_utc,
            source=source,
            instrument_like="INST-NIFTY-FUT-%",
        )
        one_minute_spot = _load_spot_interval_rows(
            conn,
            interval="1m",
            start_utc=start_utc,
            end_utc=end_utc,
            source=source,
        )
    return spot, futures, one_minute_spot


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run Strategy D S&R Momentum Breakout read-only backtest"
        )
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=_default_db_path(),
    )
    parser.add_argument(
        "--source",
        default="BREEZE",
        choices=("BREEZE", "KITE", "LIVE", "MIXED"),
    )
    parser.add_argument(
        "--start-date",
        type=date.fromisoformat,
    )
    parser.add_argument(
        "--end-date",
        type=date.fromisoformat,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            Path("data")
            / "strategy_d_sr_momentum_breakout_v2_backtest.json"
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    with _open_read_only(args.db) as conn:
        first, last = _available_spot_dates(
            conn,
            args.source,
        )
    start_date = args.start_date or first
    end_date = args.end_date or last
    if end_date < start_date:
        raise SystemExit(
            "end-date must not precede start-date"
        )
    spot, futures, one_minute_spot = load_historical_candles(
        db_path=args.db,
        source=args.source,
        start_date=start_date,
        end_date=end_date,
    )
    report = build_v2_comparison(
        spot_candles=spot,
        futures_candles=futures,
        one_minute_spot_candles=one_minute_spot,
        start_date=start_date,
        end_date=end_date,
    )
    args.output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    args.output.write_text(
        json.dumps(
            report,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "strategy_id": report["strategy_id"],
                "metrics": report["metrics"],
                "usable_sessions": report["usable_sessions"],
                "intrabar_coverage": report["intrabar_coverage"],
                "comparison_to_corrected_v1": (
                    report["comparison_to_corrected_v1"]
                ),
                "skipped": report["skipped_sessions_or_events"],
                "output": str(args.output),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
