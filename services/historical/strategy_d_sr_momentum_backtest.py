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
from services.historical.strategy_d_candidate_manifest import (
    candidate_spec,
    spec_fingerprint,
    validate_v2_config,
)
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
from services.strategy.features import FeatureEngine
from services.strategy.strategies.sr_momentum_breakout import (
    REAL_SOURCES,
    StrategyDConfig,
    StrategyDPositionManager,
    _crossed_resistance,
    _crossed_support,
    _futures_vwap_confirmation,
    _in_entry_window,
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


def _diagnose_strategy_d_bar(
    *,
    spot_history: Sequence[Candle],
    futures_history: Sequence[Candle],
    levels: Any,
    config: StrategyDConfig,
    signal: Any,
) -> dict[str, Any]:
    """Observe Strategy D qualification without changing signal behavior."""
    conditions: dict[str, bool] = {}
    values: dict[str, Any] = {}
    blocker = "UNKNOWN"

    if len(spot_history) < config.rsi_period + 2:
        return {
            "primary_blocker": "HISTORY_NOT_READY",
            "conditions": {"history_ready": False},
            "values": values,
            "breakout_direction": None,
            "qualified_signal": signal is not None,
        }

    conditions["history_ready"] = True
    current = spot_history[-1]
    previous = spot_history[-2]
    in_window = _in_entry_window(current.end_time, config)
    conditions["entry_window"] = in_window

    closes = [float(bar.close) for bar in spot_history]
    previous_rsi = FeatureEngine.calculate_rsi(
        closes[:-1],
        config.rsi_period,
    )
    current_rsi = FeatureEngine.calculate_rsi(
        closes,
        config.rsi_period,
    )
    values["rsi_previous"] = round(float(previous_rsi), 6)
    values["rsi_current"] = round(float(current_rsi), 6)
    outside_trap = not (
        config.trap_rsi_low
        <= current_rsi
        <= config.trap_rsi_high
    )
    conditions["rsi_outside_trap_zone"] = outside_trap

    atr = FeatureEngine.calculate_atr(
        list(spot_history),
        config.atr_period,
    )
    conditions["atr_valid"] = atr > 0
    values["atr_5m"] = round(float(atr), 6)

    range_pass = False
    previous_day_range_atr = None
    if atr > 0:
        previous_day_range_atr = (
            levels.pdh - levels.pdl
        ) / float(atr)
        values["previous_day_range_atr"] = round(
            float(previous_day_range_atr),
            6,
        )
        range_pass = (
            config.max_previous_day_range_atr is None
            or previous_day_range_atr
            < config.max_previous_day_range_atr
        )
    conditions["previous_day_range_pass"] = range_pass

    confirmation = _futures_vwap_confirmation(
        futures_history,
        through=current.end_time,
    )
    conditions["futures_vwap_available"] = confirmation is not None
    futures_price = None
    vwap = None
    if confirmation is not None:
        futures_price, vwap = confirmation
        values["futures_price"] = round(float(futures_price), 6)
        values["futures_vwap"] = round(float(vwap), 6)

    resistance = _crossed_resistance(
        float(previous.close),
        float(current.close),
        levels,
    )
    support = _crossed_support(
        float(previous.close),
        float(current.close),
        levels,
    )
    conditions["resistance_breakout"] = resistance is not None
    conditions["support_breakout"] = support is not None
    structural = resistance is not None or support is not None
    conditions["structural_breakout"] = structural

    breakout_direction = (
        "CALL"
        if resistance is not None
        else "PUT"
        if support is not None
        else None
    )
    values["breakout_level"] = (
        resistance[0]
        if resistance is not None
        else support[0]
        if support is not None
        else None
    )

    long_current = (
        current_rsi
        > config.long_rsi_cross
        + config.minimum_rsi_clearance_points
    )
    short_current = (
        current_rsi
        < config.short_rsi_cross
        - config.minimum_rsi_clearance_points
    )
    long_cross = previous_rsi <= config.long_rsi_cross and long_current
    short_cross = previous_rsi >= config.short_rsi_cross and short_current
    long_already = previous_rsi > config.long_rsi_cross and long_current
    short_already = previous_rsi < config.short_rsi_cross and short_current

    conditions["long_rsi_current_qualified"] = long_current
    conditions["short_rsi_current_qualified"] = short_current
    conditions["long_rsi_cross_same_bar"] = long_cross
    conditions["short_rsi_cross_same_bar"] = short_cross
    conditions["long_rsi_already_qualified"] = long_already
    conditions["short_rsi_already_qualified"] = short_already

    vwap_aligned = False
    rsi_current_aligned = False
    rsi_cross_aligned = False
    rsi_already_aligned = False
    if resistance is not None:
        vwap_aligned = (
            confirmation is not None
            and futures_price > vwap
        )
        rsi_current_aligned = long_current
        rsi_cross_aligned = long_cross
        rsi_already_aligned = long_already
    elif support is not None:
        vwap_aligned = (
            confirmation is not None
            and futures_price < vwap
        )
        rsi_current_aligned = short_current
        rsi_cross_aligned = short_cross
        rsi_already_aligned = short_already

    conditions["breakout_vwap_aligned"] = structural and vwap_aligned
    conditions["breakout_rsi_current_qualified"] = (
        structural and rsi_current_aligned
    )
    conditions["breakout_rsi_cross_same_bar"] = (
        structural and rsi_cross_aligned
    )
    conditions["breakout_rsi_already_qualified"] = (
        structural and rsi_already_aligned
    )
    conditions["breakout_vwap_and_rsi_current"] = (
        structural and vwap_aligned and rsi_current_aligned
    )
    conditions["breakout_vwap_and_rsi_cross"] = (
        structural and vwap_aligned and rsi_cross_aligned
    )
    conditions["qualified_signal"] = signal is not None

    if not in_window:
        blocker = "OUTSIDE_ENTRY_WINDOW"
    elif not outside_trap:
        blocker = "RSI_TRAP_ZONE"
    elif atr <= 0:
        blocker = "ATR_INVALID"
    elif not range_pass:
        blocker = "PREVIOUS_DAY_RANGE_FILTER"
    elif confirmation is None:
        blocker = "FUTURES_VWAP_UNAVAILABLE"
    elif not structural:
        blocker = "NO_STRUCTURAL_BREAKOUT"
    elif not vwap_aligned:
        blocker = "FUTURES_VWAP_MISALIGNED"
    elif rsi_already_aligned:
        blocker = "RSI_ALREADY_QUALIFIED_BEFORE_BREAKOUT"
    elif not rsi_cross_aligned:
        blocker = "RSI_CROSS_NOT_CONFIRMED"
    elif signal is None:
        blocker = "UNCLASSIFIED_SIGNAL_REJECTION"
    else:
        blocker = "QUALIFIED_SIGNAL"

    return {
        "primary_blocker": blocker,
        "conditions": conditions,
        "values": values,
        "breakout_direction": breakout_direction,
        "qualified_signal": signal is not None,
    }


def _signal_diagnostic_report(
    *,
    condition_counts: Counter[str],
    blocker_counts: Counter[str],
    breakout_counts: Counter[str],
    per_session: dict[str, dict[str, Any]],
    bars_evaluated: int,
) -> dict[str, Any]:
    structural = condition_counts["structural_breakout"]
    return {
        "bars_evaluated": bars_evaluated,
        "condition_counts": dict(sorted(condition_counts.items())),
        "primary_blocker_counts": dict(sorted(blocker_counts.items())),
        "breakout_direction_counts": dict(sorted(breakout_counts.items())),
        "structural_breakouts": structural,
        "breakout_with_vwap_alignment": (
            condition_counts["breakout_vwap_aligned"]
        ),
        "breakout_with_rsi_current_qualified": (
            condition_counts["breakout_rsi_current_qualified"]
        ),
        "breakout_with_rsi_cross_same_bar": (
            condition_counts["breakout_rsi_cross_same_bar"]
        ),
        "breakout_with_rsi_already_qualified": (
            condition_counts["breakout_rsi_already_qualified"]
        ),
        "breakout_with_vwap_and_rsi_current": (
            condition_counts["breakout_vwap_and_rsi_current"]
        ),
        "breakout_with_vwap_and_rsi_cross": (
            condition_counts["breakout_vwap_and_rsi_cross"]
        ),
        "qualified_signal_bars": condition_counts["qualified_signal"],
        "per_session": {
            day: {
                "bars_evaluated": row["bars_evaluated"],
                "structural_breakouts": row["structural_breakouts"],
                "qualified_signal_bars": row["qualified_signal_bars"],
                "primary_blocker_counts": dict(
                    sorted(row["primary_blocker_counts"].items())
                ),
            }
            for day, row in sorted(per_session.items())
        },
        "interpretation_note": (
            "RSI-current-qualified counts breakouts where RSI was already "
            "beyond the directional threshold or crossed it on that bar; "
            "RSI-cross-same-bar counts only the stricter production entry "
            "condition. Their gap directly measures breakout bars rejected "
            "because RSI crossed earlier."
        ),
    }


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


def _session_summary(
    trades: Sequence[dict[str, Any]],
    usable_session_dates: Sequence[date],
) -> dict[str, Any]:
    trade_counts = Counter(str(row["date"]) for row in trades)
    session_dates = [day.isoformat() for day in usable_session_dates]
    trade_days = sum(trade_counts.get(day, 0) > 0 for day in session_dates)
    return {
        "session_count": len(session_dates),
        "session_dates": session_dates,
        "trade_days": trade_days,
        "no_trade_days": len(session_dates) - trade_days,
        "trades_by_date": {
            day: trade_counts.get(day, 0)
            for day in session_dates
        },
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
        "mean_mfe_r": (
            round(mean(float(row["mfe_r"]) for row in trades), 6)
            if trades
            else None
        ),
        "mean_mae_r": (
            round(mean(float(row["mae_r"]) for row in trades), 6)
            if trades
            else None
        ),
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
    session_dates: Sequence[date] | None = None,
    config: StrategyDConfig | None = None,
) -> dict[str, Any]:
    """Run one frozen Strategy D ruleset on preloaded real candles."""
    cfg = config or StrategyDConfig.v1_control()
    if cfg.variant == "V2_CANDIDATE":
        validate_v2_config(cfg)
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
    if session_dates is not None:
        requested_days = set(session_dates)
        available_days = [
            day for day in available_days if day in requested_days
        ]

    manager = StrategyDPositionManager(strategy_d_config=cfg)
    trades: list[dict[str, Any]] = []
    levels_rows: list[dict[str, Any]] = []
    usable_sessions = 0
    usable_session_dates: list[date] = []
    usable_sessions_with_1m = 0
    skipped: Counter[str] = Counter()
    diagnostic_conditions: Counter[str] = Counter()
    diagnostic_blockers: Counter[str] = Counter()
    diagnostic_breakouts: Counter[str] = Counter()
    diagnostic_sessions: dict[str, dict[str, Any]] = {}
    diagnostic_bars_evaluated = 0

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
        usable_session_dates.append(day)
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
            diagnostic = _diagnose_strategy_d_bar(
                spot_history=history,
                futures_history=futures_history,
                levels=levels,
                config=cfg,
                signal=signal,
            )
            diagnostic_bars_evaluated += 1
            for name, passed in diagnostic["conditions"].items():
                if passed:
                    diagnostic_conditions[name] += 1
            diagnostic_blockers[diagnostic["primary_blocker"]] += 1
            if diagnostic["breakout_direction"] is not None:
                diagnostic_breakouts[
                    diagnostic["breakout_direction"]
                ] += 1
            day_key = day.isoformat()
            day_diag = diagnostic_sessions.setdefault(
                day_key,
                {
                    "bars_evaluated": 0,
                    "structural_breakouts": 0,
                    "qualified_signal_bars": 0,
                    "primary_blocker_counts": Counter(),
                },
            )
            day_diag["bars_evaluated"] += 1
            if diagnostic["conditions"].get("structural_breakout"):
                day_diag["structural_breakouts"] += 1
            if diagnostic["qualified_signal"]:
                day_diag["qualified_signal_bars"] += 1
            day_diag["primary_blocker_counts"][
                diagnostic["primary_blocker"]
            ] += 1

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
        "candidate_spec": (
            candidate_spec()
            if cfg.variant == "V2_CANDIDATE"
            else None
        ),
        "candidate_spec_fingerprint": (
            spec_fingerprint()
            if cfg.variant == "V2_CANDIDATE"
            else None
        ),
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
                "back to explicitly labelled conservative 5m ordering"
            ),
            "option_pnl": "NOT_SYNTHESIZED",
            "lot_execution": (
                "runtime uses selected contract lot_size; "
                "PositionManager whole-lot sizing is reused"
            ),
        },
        "metrics": _metrics(trades, usable_sessions),
        "signal_diagnostics": _signal_diagnostic_report(
            condition_counts=diagnostic_conditions,
            blocker_counts=diagnostic_blockers,
            breakout_counts=diagnostic_breakouts,
            per_session=diagnostic_sessions,
            bars_evaluated=diagnostic_bars_evaluated,
        ),
        "usable_sessions": usable_sessions,
        "session_summary": _session_summary(
            trades,
            usable_session_dates,
        ),
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
    session_dates: Sequence[date] | None = None,
) -> dict[str, Any]:
    """Return V2 as the main report with corrected V1 metrics beside it."""
    control = run_backtest(
        spot_candles=spot_candles,
        futures_candles=futures_candles,
        one_minute_spot_candles=one_minute_spot_candles,
        start_date=start_date,
        end_date=end_date,
        session_dates=session_dates,
        config=StrategyDConfig.v1_control(),
    )
    candidate = run_backtest(
        spot_candles=spot_candles,
        futures_candles=futures_candles,
        one_minute_spot_candles=one_minute_spot_candles,
        start_date=start_date,
        end_date=end_date,
        session_dates=session_dates,
        config=StrategyDConfig.v2_candidate(),
    )
    control_metrics = control["metrics"]
    candidate_metrics = candidate["metrics"]
    candidate["comparison_to_corrected_v1"] = {
        "control_strategy_id": control["strategy_id"],
        "control_config": control["config"],
        "control_metrics": control_metrics,
        "control_signal_diagnostics": control["signal_diagnostics"],
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


def _available_usable_session_dates(
    conn: Any,
    source: str,
) -> list[date]:
    """Return dates with both NIFTY spot and futures 5m data.

    The first spot date is excluded because Strategy D requires previous-session
    levels. Final usability is still verified by the backtest after candle
    loading, so this helper is only the cheap database-side candidate filter.
    """
    source_clause, source_params = _source_predicate(source)
    rows = conn.execute(
        f"""
        SELECT instrument_id, start_time
        FROM historical_candles
        WHERE interval = '5m'
          AND (
            instrument_id = 'INST-NIFTY-INDEX'
            OR instrument_id LIKE 'INST-NIFTY-FUT-%'
          )
          AND {source_clause}
        ORDER BY start_time ASC
        """,
        source_params,
    ).fetchall()
    spot_dates: set[date] = set()
    futures_dates: set[date] = set()
    for row in rows:
        day = _aware(row["start_time"]).astimezone(IST).date()
        if row["instrument_id"] == "INST-NIFTY-INDEX":
            spot_dates.add(day)
        else:
            futures_dates.add(day)
    if not spot_dates:
        return []
    first_spot_date = min(spot_dates)
    return sorted(
        day
        for day in spot_dates & futures_dates
        if day > first_spot_date
    )


def _select_requested_session_dates(
    available_dates: Sequence[date],
    *,
    sessions: int,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[date]:
    """Select an exact research-session window deterministically."""
    if sessions <= 0:
        raise ValueError("sessions must be greater than zero")
    eligible = sorted(set(available_dates))
    if start_date is not None:
        eligible = [day for day in eligible if day >= start_date]
    if end_date is not None:
        eligible = [day for day in eligible if day <= end_date]
    if len(eligible) < sessions:
        raise ValueError(
            f"requested {sessions} usable sessions but only "
            f"{len(eligible)} candidate sessions are available"
        )
    if start_date is not None:
        return eligible[:sessions]
    return eligible[-sessions:]


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
        "--sessions",
        type=int,
        help=(
            "Run exactly this many usable sessions. Without --start-date, "
            "the latest eligible sessions are selected; with --start-date, "
            "selection proceeds forward from that date."
        ),
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
        available_session_dates = (
            _available_usable_session_dates(conn, args.source)
            if args.sessions is not None
            else []
        )

    selected_session_dates: list[date] | None = None
    if args.sessions is not None:
        try:
            selected_session_dates = _select_requested_session_dates(
                available_session_dates,
                sessions=args.sessions,
                start_date=args.start_date,
                end_date=args.end_date,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        start_date = selected_session_dates[0]
        end_date = selected_session_dates[-1]
    else:
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
        session_dates=selected_session_dates,
    )
    if (
        args.sessions is not None
        and report["usable_sessions"] != args.sessions
    ):
        raise SystemExit(
            "candidate-date selection did not produce exactly "
            f"{args.sessions} usable sessions; got "
            f"{report['usable_sessions']}. "
            "Review skipped_sessions_or_events and data coverage."
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
                "signal_diagnostics": report["signal_diagnostics"],
                "usable_sessions": report["usable_sessions"],
                "session_summary": report["session_summary"],
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
