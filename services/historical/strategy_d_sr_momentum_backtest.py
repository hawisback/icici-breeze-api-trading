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
from collections import Counter
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
    _source_predicate,
)
from services.strategy.strategies.sr_momentum_breakout import (
    REAL_SOURCES,
    STRATEGY_D_ID,
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


def _group_by_day(candles: Sequence[Candle]) -> dict[date, list[Candle]]:
    result: dict[date, list[Candle]] = {}
    for candle in candles:
        if candle.interval != "5m" or candle.source not in REAL_SOURCES:
            continue
        result.setdefault(_session_date(candle), []).append(candle)
    for day in result:
        result[day].sort(key=lambda item: item.start_time)
    return result


def _active_futures_by_day(candles: Sequence[Candle]) -> dict[date, list[Candle]]:
    grouped = _group_by_day(candles)
    result: dict[date, list[Candle]] = {}
    for day, bars in grouped.items():
        contract = _active_contract(day, bars)
        if contract:
            result[day] = [bar for bar in bars if bar.instrument_id == contract]
    return result


def _metrics(trades: Sequence[dict[str, Any]], usable_sessions: int) -> dict[str, Any]:
    values = [float(row["realized_r"]) for row in trades]
    wins = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    max_losing_streak = 0
    losing_streak = 0
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
    }


def run_backtest(
    *,
    spot_candles: Sequence[Candle],
    futures_candles: Sequence[Candle],
    start_date: date | None = None,
    end_date: date | None = None,
    config: StrategyDConfig | None = None,
) -> dict[str, Any]:
    """Run Strategy D V1 on preloaded real historical candles."""
    cfg = config or StrategyDConfig()
    spot = sorted(spot_candles, key=lambda item: item.start_time)
    spot_by_day = _group_by_day(spot)
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
    skipped: Counter[str] = Counter()

    for day in available_days:
        day_spot = spot_by_day.get(day, [])
        day_futures = futures_by_day.get(day, [])
        levels = previous_session_levels(spot, day)
        if levels is None:
            skipped["NO_PREVIOUS_SESSION_LEVELS"] += 1
            continue
        if not day_futures:
            skipped["NO_ACTIVE_FUTURES_5M"] += 1
            continue
        usable_sessions += 1
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
            )
            used_levels.add(signal_key)
            row = {
                "strategy_id": STRATEGY_D_ID,
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
        "strategy_id": STRATEGY_D_ID,
        "ruleset_version": 1,
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
            "sources_allowed": sorted(REAL_SOURCES),
            "lookahead": False,
        },
        "execution_model": {
            "entry": (
                "completed 5m spot close after structural break + "
                "RSI cross + futures VWAP alignment"
            ),
            "initial_stop": (
                "1.5 x spot 5m ATR on the adverse side"
            ),
            "scale_out": (
                "idealized 50% at +1.5R for underlying research"
            ),
            "runner_stop_after_scale": "breakeven at entry",
            "runner_exit": (
                "completed 5m close across EMA9, then R2/S2 if "
                "beyond +1.5R, or session force exit"
            ),
            "same_bar_ambiguity": (
                "protective stop first on 5m OHLC"
            ),
            "option_pnl": "NOT_SYNTHESIZED",
            "lot_execution": (
                "runtime uses selected contract lot_size; "
                "PositionManager whole-lot sizing is reused"
            ),
        },
        "metrics": _metrics(trades, usable_sessions),
        "usable_sessions": usable_sessions,
        "skipped_sessions_or_events": dict(sorted(skipped.items())),
        "levels": levels_rows,
        "trades": trades,
        "limitations": [
            (
                "This V1 report validates the signal and underlying "
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
                "belong to the next validation layer."
            ),
            (
                "Same 5m bar stop/target ambiguity is resolved stop-first "
                "unless a later 1m execution layer proves ordering."
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


def load_historical_candles(
    *,
    db_path: Path,
    source: str,
    start_date: date,
    end_date: date,
) -> tuple[list[Candle], list[Candle]]:
    """Read only the candles required by Strategy D plus warm-up history."""
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
    with _open_read_only(db_path) as conn:
        spot = _load_rows(
            conn,
            start_utc=start_ist.astimezone(timezone.utc),
            end_utc=end_ist.astimezone(timezone.utc),
            source=source,
            instrument_id="INST-NIFTY-INDEX",
        )
        futures = _load_rows(
            conn,
            start_utc=start_ist.astimezone(timezone.utc),
            end_utc=end_ist.astimezone(timezone.utc),
            source=source,
            instrument_like="INST-NIFTY-FUT-%",
        )
    return spot, futures


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
            / "strategy_d_sr_momentum_breakout_backtest.json"
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
    spot, futures = load_historical_candles(
        db_path=args.db,
        source=args.source,
        start_date=start_date,
        end_date=end_date,
    )
    report = run_backtest(
        spot_candles=spot,
        futures_candles=futures,
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
                "skipped": report["skipped_sessions_or_events"],
                "output": str(args.output),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
