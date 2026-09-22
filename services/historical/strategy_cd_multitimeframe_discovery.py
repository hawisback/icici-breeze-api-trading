"""Native multi-timeframe discovery for companion Strategy C/D candidates.

Research-only harness:
- completed 15m context derived from completed native 5m futures candles,
- completed native 5m setup detection,
- native 1m triggers that can only begin after the 5m setup closes,
- native 1m stop/target/breakeven/trailing lifecycle.

The module never changes Strategy A V3, writes market data, or calls a broker.
Results are futures/underlying research R, not executable historical option P&L.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Sequence

from libs.contracts.models import Candle
from services.historical.strategy_a_data_audit import (
    IST,
    OPTIONAL_CLOSE_MARKER,
    SESSION_START,
    WARMUP_CALENDAR_DAYS,
    _default_db_path,
    _open_read_only,
    _row_to_candle,
    _source_predicate,
)
from services.historical.strategy_a_research import _session_dates
from services.historical.strategy_a_v3_momentum_validation import build_validation as build_v3_validation
from services.strategy.futures_signal import (
    FuturesFeatureEngine,
    aggregate_completed_15m,
    canonical_active_futures_stream,
)


SETUP_FIRST_END = time(9, 45)
SETUP_LAST_END = time(14, 45)
FORCED_EXIT = time(15, 15)
FAMILIES = (
    "vwap_reclaim",
    "ema_reclaim_pullback",
    "di_continuation",
    "micro_breakout_retest",
    "controlled_vwap_mean_reversion",
)


@dataclass(frozen=True)
class DiscoveryConfig:
    trigger_validity_minutes: int = 5
    stop_buffer_atr: float = 0.05
    max_chase_atr: float = 0.20
    min_risk_atr: float = 0.15
    max_risk_atr: float = 1.25
    hard_target_r: float = 2.0
    breakeven_arm_r: float = 1.0
    trail_arm_r: float = 1.5
    trail_offset_r: float = 0.75
    max_trades_per_day: int = 2


@dataclass(frozen=True)
class MTFFeature:
    candle: Candle
    ema20: float
    ema50: float
    adx14: float
    plus_di14: float
    minus_di14: float
    atr14: float
    session_vwap: float


@dataclass(frozen=True)
class Trigger:
    family: str
    direction: str
    setup_end: datetime
    entry_bar: Candle
    entry_price: float
    initial_stop: float
    setup_atr: float


@dataclass(frozen=True)
class Trade:
    family: str
    direction: str
    setup_end: datetime
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    initial_stop: float
    exit_price: float
    realized_r: float
    mfe_r: float
    mae_r: float
    exit_reason: str

    @property
    def day(self) -> str:
        return self.entry_time.astimezone(IST).date().isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "direction": self.direction,
            "date": self.day,
            "setup_end": self.setup_end.isoformat(),
            "entry_time": self.entry_time.isoformat(),
            "entry_time_ist": self.entry_time.astimezone(IST).isoformat(),
            "exit_time": self.exit_time.isoformat(),
            "entry_price": round(self.entry_price, 6),
            "initial_stop": round(self.initial_stop, 6),
            "exit_price": round(self.exit_price, 6),
            "realized_r": round(self.realized_r, 6),
            "mfe_r": round(self.mfe_r, 6),
            "mae_r": round(self.mae_r, 6),
            "exit_reason": self.exit_reason,
        }


def _load_interval_rows(
    conn: Any,
    *,
    interval: str,
    start_utc: datetime,
    end_utc: datetime,
    source: str,
) -> list[Candle]:
    clauses = [
        "interval = ?",
        "instrument_id LIKE 'INST-NIFTY-FUT-%'",
        "start_time >= ?",
        "start_time < ?",
    ]
    params: list[Any] = [interval, start_utc.isoformat(), end_utc.isoformat()]
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


def _feature(history: Sequence[Candle]) -> MTFFeature:
    if not history:
        raise ValueError("feature history must not be empty")
    candle = history[-1]
    adx, plus_di, minus_di = FuturesFeatureEngine.adx_di(history, 14)
    return MTFFeature(
        candle=candle,
        ema20=FuturesFeatureEngine.ema([bar.close for bar in history], 20),
        ema50=FuturesFeatureEngine.ema([bar.close for bar in history], 50),
        adx14=adx,
        plus_di14=plus_di,
        minus_di14=minus_di,
        atr14=FuturesFeatureEngine.atr(history, 14),
        session_vwap=FuturesFeatureEngine.session_vwap(history),
    )


def _feature_map_for_ends(
    stream: Sequence[Candle],
    ends: set[datetime],
) -> dict[datetime, MTFFeature]:
    result: dict[datetime, MTFFeature] = {}
    history: list[Candle] = []
    for bar in stream:
        history.append(bar)
        if bar.end_time in ends:
            result[bar.end_time] = _feature(history)
    return result


def _minute(value: datetime) -> int:
    local = value.astimezone(IST)
    return local.hour * 60 + local.minute


def _in_setup_window(value: datetime) -> bool:
    minute = _minute(value)
    first = SETUP_FIRST_END.hour * 60 + SETUP_FIRST_END.minute
    last = SETUP_LAST_END.hour * 60 + SETUP_LAST_END.minute
    return first <= minute <= last and minute % 5 == 0


def _directional_body(feature: MTFFeature, direction: str) -> bool:
    if direction == "CALL":
        return feature.candle.close > feature.candle.open
    return feature.candle.close < feature.candle.open


def _body_ratio(feature: MTFFeature) -> float:
    width = feature.candle.high - feature.candle.low
    return abs(feature.candle.close - feature.candle.open) / width if width > 0 else 0.0


def _trend_context(feature: MTFFeature, direction: str) -> bool:
    if feature.atr14 <= 0:
        return False
    if direction == "CALL":
        return feature.ema20 > feature.ema50 and feature.plus_di14 > feature.minus_di14
    return feature.ema20 < feature.ema50 and feature.minus_di14 > feature.plus_di14


def _range_context(feature: MTFFeature) -> bool:
    if feature.atr14 <= 0:
        return False
    separation = abs(feature.ema20 - feature.ema50) / feature.atr14
    return feature.adx14 < 30.0 and separation <= 0.25


def _setup_matches(
    family: str,
    context: MTFFeature,
    current: MTFFeature,
    previous: MTFFeature,
    direction: str,
) -> bool:
    if current.atr14 <= 0:
        return False

    current_bar = current.candle
    previous_bar = previous.candle
    directional = _directional_body(current, direction)
    body_ok = _body_ratio(current) >= 0.25

    if family == "vwap_reclaim":
        if not (_trend_context(context, direction) and directional and body_ok):
            return False
        if direction == "CALL":
            return previous_bar.close < previous.session_vwap and current_bar.close >= current.session_vwap
        return previous_bar.close > previous.session_vwap and current_bar.close <= current.session_vwap

    if family == "ema_reclaim_pullback":
        if not (_trend_context(context, direction) and directional and body_ok):
            return False
        if direction == "CALL":
            crossed = previous_bar.close < previous.ema20 and current_bar.close >= current.ema20
            touched = current_bar.low <= current.ema20 <= current_bar.close
        else:
            crossed = previous_bar.close > previous.ema20 and current_bar.close <= current.ema20
            touched = current_bar.high >= current.ema20 >= current_bar.close
        return crossed or touched

    if family == "di_continuation":
        if not (_trend_context(context, direction) and directional and body_ok):
            return False
        if direction == "CALL":
            spread = current.plus_di14 - current.minus_di14
            return spread >= 5.0 and current_bar.close >= current.ema20 and current_bar.close >= current.session_vwap
        spread = current.minus_di14 - current.plus_di14
        return spread >= 5.0 and current_bar.close <= current.ema20 and current_bar.close <= current.session_vwap

    if family == "micro_breakout_retest":
        if not (_trend_context(context, direction) and directional and body_ok):
            return False
        if direction == "CALL":
            return current_bar.close > previous_bar.high
        return current_bar.close < previous_bar.low

    if family == "controlled_vwap_mean_reversion":
        if not (_range_context(context) and directional and body_ok):
            return False
        distance = abs(current_bar.close - current.session_vwap) / current.atr14
        if not 0.20 <= distance <= 0.80:
            return False
        if direction == "CALL":
            return current_bar.close < current.session_vwap
        return current_bar.close > current.session_vwap

    raise ValueError(f"unknown family: {family}")


def _eligible_1m(
    one_minute: Sequence[Candle],
    *,
    setup_end: datetime,
    validity_minutes: int,
) -> list[Candle]:
    cutoff = setup_end + timedelta(minutes=validity_minutes)
    return [
        bar
        for bar in one_minute
        if bar.start_time >= setup_end
        and bar.end_time <= cutoff
        and bar.end_time.astimezone(IST).date() == setup_end.astimezone(IST).date()
    ]


def _risk_ok(entry: float, stop: float, atr: float, config: DiscoveryConfig) -> bool:
    if atr <= 0:
        return False
    risk_atr = abs(entry - stop) / atr
    return config.min_risk_atr <= risk_atr <= config.max_risk_atr


def _find_trigger(
    *,
    family: str,
    direction: str,
    current: MTFFeature,
    previous: MTFFeature,
    one_minute: Sequence[Candle],
    config: DiscoveryConfig,
) -> Trigger | None:
    setup = current.candle
    eligible = _eligible_1m(
        one_minute,
        setup_end=setup.end_time,
        validity_minutes=config.trigger_validity_minutes,
    )
    if not eligible:
        return None

    atr = current.atr14
    if direction == "CALL":
        base_stop = setup.low - config.stop_buffer_atr * atr
        base_level = setup.high
    else:
        base_stop = setup.high + config.stop_buffer_atr * atr
        base_level = setup.low

    if family == "micro_breakout_retest":
        breakout_level = previous.candle.high if direction == "CALL" else previous.candle.low
        tolerance = 0.08 * atr
        retest: Candle | None = None
        for bar in eligible:
            if retest is None:
                if direction == "CALL":
                    held = bar.low <= breakout_level + tolerance and bar.close >= breakout_level
                else:
                    held = bar.high >= breakout_level - tolerance and bar.close <= breakout_level
                if held:
                    retest = bar
                continue

            directional = bar.close > bar.open if direction == "CALL" else bar.close < bar.open
            trigger_level = retest.high if direction == "CALL" else retest.low
            broke = bar.close > trigger_level if direction == "CALL" else bar.close < trigger_level
            if not (directional and broke):
                continue
            chase = bar.close - trigger_level if direction == "CALL" else trigger_level - bar.close
            if chase > config.max_chase_atr * atr:
                return None
            stop = (
                min(base_stop, retest.low - config.stop_buffer_atr * atr)
                if direction == "CALL"
                else max(base_stop, retest.high + config.stop_buffer_atr * atr)
            )
            if not _risk_ok(bar.close, stop, atr, config):
                return None
            return Trigger(
                family=family,
                direction=direction,
                setup_end=setup.end_time,
                entry_bar=bar,
                entry_price=bar.close,
                initial_stop=stop,
                setup_atr=atr,
            )
        return None

    for bar in eligible:
        directional = bar.close > bar.open if direction == "CALL" else bar.close < bar.open
        broke = bar.close > base_level if direction == "CALL" else bar.close < base_level
        if not (directional and broke):
            continue

        if family == "vwap_reclaim":
            held = bar.close >= current.session_vwap if direction == "CALL" else bar.close <= current.session_vwap
            if not held:
                continue
        elif family == "ema_reclaim_pullback":
            held = bar.close >= current.ema20 if direction == "CALL" else bar.close <= current.ema20
            if not held:
                continue
        elif family == "controlled_vwap_mean_reversion":
            before = abs(setup.close - current.session_vwap)
            after = abs(bar.close - current.session_vwap)
            if after >= before:
                continue

        chase = bar.close - base_level if direction == "CALL" else base_level - bar.close
        if chase > config.max_chase_atr * atr:
            return None
        if not _risk_ok(bar.close, base_stop, atr, config):
            return None
        return Trigger(
            family=family,
            direction=direction,
            setup_end=setup.end_time,
            entry_bar=bar,
            entry_price=bar.close,
            initial_stop=base_stop,
            setup_atr=atr,
        )
    return None


def _run_lifecycle(
    trigger: Trigger,
    one_minute: Sequence[Candle],
    config: DiscoveryConfig,
) -> Trade:
    entry = trigger.entry_price
    initial_stop = trigger.initial_stop
    risk = abs(entry - initial_stop)
    direction = trigger.direction
    day = trigger.entry_bar.end_time.astimezone(IST).date()
    cutoff = datetime.combine(day, FORCED_EXIT, tzinfo=IST)

    post = [
        bar for bar in one_minute
        if bar.start_time >= trigger.entry_bar.end_time
        and bar.end_time.astimezone(IST) <= cutoff
    ]
    active_stop = initial_stop
    target = (
        entry + config.hard_target_r * risk
        if direction == "CALL"
        else entry - config.hard_target_r * risk
    )
    mfe = 0.0
    mae = 0.0
    best_price = entry

    for bar in post:
        if direction == "CALL":
            favorable = (bar.high - entry) / risk
            adverse = (entry - bar.low) / risk
            stop_hit = bar.low <= active_stop
            target_hit = bar.high >= target
        else:
            favorable = (entry - bar.low) / risk
            adverse = (bar.high - entry) / risk
            stop_hit = bar.high >= active_stop
            target_hit = bar.low <= target

        mfe = max(mfe, favorable)
        mae = max(mae, adverse)

        # Conservative OHLC ordering: a protective stop wins same-minute
        # ambiguity. Trailing changes below only become active next minute.
        if stop_hit:
            realized = (
                (active_stop - entry) / risk
                if direction == "CALL"
                else (entry - active_stop) / risk
            )
            return Trade(
                family=trigger.family,
                direction=direction,
                setup_end=trigger.setup_end,
                entry_time=trigger.entry_bar.end_time,
                exit_time=bar.end_time,
                entry_price=entry,
                initial_stop=initial_stop,
                exit_price=active_stop,
                realized_r=realized,
                mfe_r=mfe,
                mae_r=mae,
                exit_reason="STOP_OR_TRAIL",
            )
        if target_hit:
            return Trade(
                family=trigger.family,
                direction=direction,
                setup_end=trigger.setup_end,
                entry_time=trigger.entry_bar.end_time,
                exit_time=bar.end_time,
                entry_price=entry,
                initial_stop=initial_stop,
                exit_price=target,
                realized_r=config.hard_target_r,
                mfe_r=mfe,
                mae_r=mae,
                exit_reason="HARD_TARGET",
            )

        if direction == "CALL":
            best_price = max(best_price, bar.high)
            best_r = (best_price - entry) / risk
            next_stop = active_stop
            if best_r >= config.breakeven_arm_r:
                next_stop = max(next_stop, entry)
            if best_r >= config.trail_arm_r:
                next_stop = max(next_stop, best_price - config.trail_offset_r * risk)
            active_stop = next_stop
        else:
            best_price = min(best_price, bar.low)
            best_r = (entry - best_price) / risk
            next_stop = active_stop
            if best_r >= config.breakeven_arm_r:
                next_stop = min(next_stop, entry)
            if best_r >= config.trail_arm_r:
                next_stop = min(next_stop, best_price + config.trail_offset_r * risk)
            active_stop = next_stop

    if post:
        exit_bar = post[-1]
        exit_price = exit_bar.close
        exit_time = exit_bar.end_time
    else:
        exit_price = entry
        exit_time = trigger.entry_bar.end_time
    realized = (
        (exit_price - entry) / risk
        if direction == "CALL"
        else (entry - exit_price) / risk
    )
    return Trade(
        family=trigger.family,
        direction=direction,
        setup_end=trigger.setup_end,
        entry_time=trigger.entry_bar.end_time,
        exit_time=exit_time,
        entry_price=entry,
        initial_stop=initial_stop,
        exit_price=exit_price,
        realized_r=realized,
        mfe_r=mfe,
        mae_r=mae,
        exit_reason="FORCED_EXIT",
    )


def _quarter(day: str) -> str:
    month = int(day[5:7])
    return f"{day[:4]}-Q{((month - 1) // 3) + 1}"


def _max_drawdown(values: Sequence[float]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return round(drawdown, 6)


def _max_losing_streak(values: Sequence[float]) -> int:
    current = best = 0
    for value in values:
        if value < 0:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best


def _period_metrics(trades: Sequence[Trade]) -> dict[str, Any]:
    values = [trade.realized_r for trade in trades]
    wins = sum(value > 0 for value in values)
    return {
        "trades": len(trades),
        "win_rate_pct": round(100 * wins / len(values), 2) if values else 0.0,
        "mean_r": round(mean(values), 6) if values else None,
        "total_r": round(sum(values), 6),
    }


def _frequency_band(trades_per_session: float) -> str:
    if trades_per_session < 0.27:
        return "~1 trade / 5 sessions"
    if trades_per_session < 0.42:
        return "~1 trade / 3 sessions"
    if trades_per_session < 0.75:
        return "~1 trade / 2 sessions"
    if trades_per_session < 1.40:
        return "~1 trade / session"
    return "~1-2 trades / session"


def _metrics(trades: Sequence[Trade], *, usable_sessions: int) -> dict[str, Any]:
    ordered = sorted(trades, key=lambda trade: trade.entry_time)
    values = [trade.realized_r for trade in ordered]
    gross_profit = sum(value for value in values if value > 0)
    gross_loss = abs(sum(value for value in values if value < 0))
    by_month: dict[str, list[Trade]] = defaultdict(list)
    by_quarter: dict[str, list[Trade]] = defaultdict(list)
    by_hour: dict[str, list[Trade]] = defaultdict(list)
    for trade in ordered:
        by_month[trade.day[:7]].append(trade)
        by_quarter[_quarter(trade.day)].append(trade)
        local = trade.entry_time.astimezone(IST)
        by_hour[f"{local.hour:02d}:00"] .append(trade)

    tps = len(ordered) / usable_sessions if usable_sessions else 0.0
    return {
        "trades": len(ordered),
        "trades_per_10_sessions": round(tps * 10, 3),
        "trades_per_session": round(tps, 4),
        "frequency_band": _frequency_band(tps),
        "win_rate_pct": round(100 * sum(value > 0 for value in values) / len(values), 2) if values else 0.0,
        "mean_r": round(mean(values), 6) if values else None,
        "total_r": round(sum(values), 6),
        "max_drawdown_r": _max_drawdown(values),
        "max_losing_streak": _max_losing_streak(values),
        "profit_factor": round(gross_profit / gross_loss, 6) if gross_loss > 0 else None,
        "mean_mfe_r": round(mean([trade.mfe_r for trade in ordered]), 6) if ordered else None,
        "median_mfe_r": round(median([trade.mfe_r for trade in ordered]), 6) if ordered else None,
        "mean_mae_r": round(mean([trade.mae_r for trade in ordered]), 6) if ordered else None,
        "median_mae_r": round(median([trade.mae_r for trade in ordered]), 6) if ordered else None,
        "direction_counts": dict(sorted(Counter(trade.direction for trade in ordered).items())),
        "exit_reasons": dict(sorted(Counter(trade.exit_reason for trade in ordered).items())),
        "time_of_day": {key: _period_metrics(value) for key, value in sorted(by_hour.items())},
        "monthly": {key: _period_metrics(value) for key, value in sorted(by_month.items())},
        "quarterly": {key: _period_metrics(value) for key, value in sorted(by_quarter.items())},
    }


def _folds(dates: Sequence[str], train: int = 120, test: int = 40) -> list[list[str]]:
    unique = sorted(set(dates))
    result: list[list[str]] = []
    cursor = 0
    while cursor + train + test <= len(unique):
        result.append(unique[cursor + train:cursor + train + test])
        cursor += test
    return result


def _build_day_trades(
    *,
    day: date,
    raw_5m: Sequence[Candle],
    raw_1m: Sequence[Candle],
    config: DiscoveryConfig,
) -> tuple[dict[str, list[Trade]], dict[str, int], str | None]:
    as_of = datetime.combine(day, OPTIONAL_CLOSE_MARKER, tzinfo=IST) + timedelta(minutes=5)
    stream_5m = canonical_active_futures_stream(raw_5m, as_of=as_of, interval="5m")
    stream_15m = canonical_active_futures_stream(
        aggregate_completed_15m(raw_5m, as_of=as_of),
        as_of=as_of,
        interval="15m",
    )
    stream_1m = canonical_active_futures_stream(raw_1m, as_of=as_of, interval="1m")
    setup_bars = [
        bar for bar in stream_5m
        if bar.end_time.astimezone(IST).date() == day and _in_setup_window(bar.end_time)
    ]
    if not setup_bars:
        return {name: [] for name in FAMILIES}, {name: 0 for name in FAMILIES}, "NO_5M_SETUP_WINDOW"
    if len(stream_1m) < 300:
        return {name: [] for name in FAMILIES}, {name: 0 for name in FAMILIES}, f"THIN_1M_SESSION:{len(stream_1m)}"

    first_setup = setup_bars[0].end_time
    warmup_5m = sum(bar.end_time <= first_setup for bar in stream_5m)
    warmup_15m = sum(bar.end_time <= first_setup for bar in stream_15m)
    if warmup_5m < 50 or warmup_15m < 50:
        return (
            {name: [] for name in FAMILIES},
            {name: 0 for name in FAMILIES},
            f"INSUFFICIENT_WARMUP:5m={warmup_5m},15m={warmup_15m}",
        )

    setup_ends = {bar.end_time for bar in setup_bars}
    index_5m = {bar.end_time: i for i, bar in enumerate(stream_5m)}
    previous_ends = {
        stream_5m[index_5m[end] - 1].end_time
        for end in setup_ends
        if index_5m[end] > 0
    }
    features_5m = _feature_map_for_ends(stream_5m, setup_ends | previous_ends)

    context_end_for_setup: dict[datetime, datetime] = {}
    context_ends: set[datetime] = set()
    for setup in setup_bars:
        eligible = [bar.end_time for bar in stream_15m if bar.end_time <= setup.end_time]
        if eligible:
            context_end_for_setup[setup.end_time] = eligible[-1]
            context_ends.add(eligible[-1])

    # Existing Strategy A feature engine remains the canonical 15m context
    # implementation. We only build snapshots at context bars actually used.
    features_15m: dict[datetime, MTFFeature] = {}
    history_15m: list[Candle] = []
    for bar in stream_15m:
        history_15m.append(bar)
        if bar.end_time not in context_ends:
            continue
        snap = FuturesFeatureEngine.build(history_15m, as_of=bar.end_time)
        features_15m[bar.end_time] = MTFFeature(
            candle=bar,
            ema20=snap.ema20,
            ema50=snap.ema50,
            adx14=snap.adx14,
            plus_di14=snap.plus_di14,
            minus_di14=snap.minus_di14,
            atr14=snap.atr14,
            session_vwap=snap.session_vwap,
        )

    by_family: dict[str, list[Trade]] = {name: [] for name in FAMILIES}
    setup_counts: dict[str, int] = {name: 0 for name in FAMILIES}
    blocked_until: dict[str, datetime | None] = {name: None for name in FAMILIES}

    for setup in setup_bars:
        idx = index_5m[setup.end_time]
        if idx == 0:
            continue
        current = features_5m.get(setup.end_time)
        previous = features_5m.get(stream_5m[idx - 1].end_time)
        context_end = context_end_for_setup.get(setup.end_time)
        context = features_15m.get(context_end) if context_end is not None else None
        if current is None or previous is None or context is None:
            continue

        for family in FAMILIES:
            if len(by_family[family]) >= config.max_trades_per_day:
                continue
            for direction in ("CALL", "PUT"):
                if not _setup_matches(family, context, current, previous, direction):
                    continue
                setup_counts[family] += 1
                trigger = _find_trigger(
                    family=family,
                    direction=direction,
                    current=current,
                    previous=previous,
                    one_minute=stream_1m,
                    config=config,
                )
                if trigger is None:
                    continue
                blocked = blocked_until[family]
                if blocked is not None and trigger.entry_bar.end_time < blocked:
                    continue
                trade = _run_lifecycle(trigger, stream_1m, config)
                by_family[family].append(trade)
                blocked_until[family] = trade.exit_time
                break

    return by_family, setup_counts, None


def build_report(
    db_path: Path,
    *,
    source: str = "BREEZE",
    sessions: int = 0,
    include_v3_overlap: bool = True,
    config: DiscoveryConfig | None = None,
) -> dict[str, Any]:
    cfg = config or DiscoveryConfig()
    conn = _open_read_only(db_path)
    family_trades: dict[str, list[Trade]] = {name: [] for name in FAMILIES}
    family_setup_counts: Counter[str] = Counter()
    skipped: list[dict[str, str]] = []
    usable_dates: list[str] = []
    try:
        dates = _session_dates(conn, sessions=sessions, source=source)
        for day in dates:
            warmup_start = datetime.combine(
                day - timedelta(days=WARMUP_CALENDAR_DAYS),
                SESSION_START,
                tzinfo=IST,
            )
            session_end = datetime.combine(day, OPTIONAL_CLOSE_MARKER, tzinfo=IST) + timedelta(minutes=5)
            raw_5m = _load_interval_rows(
                conn,
                interval="5m",
                start_utc=warmup_start.astimezone(timezone.utc),
                end_utc=session_end.astimezone(timezone.utc),
                source=source,
            )
            raw_1m = _load_interval_rows(
                conn,
                interval="1m",
                start_utc=datetime.combine(day, SESSION_START, tzinfo=IST).astimezone(timezone.utc),
                end_utc=session_end.astimezone(timezone.utc),
                source=source,
            )
            day_trades, setup_counts, reason = _build_day_trades(
                day=day,
                raw_5m=raw_5m,
                raw_1m=raw_1m,
                config=cfg,
            )
            if reason is not None:
                skipped.append({"date": day.isoformat(), "reason": reason})
                continue
            usable_dates.append(day.isoformat())
            family_setup_counts.update(setup_counts)
            for family, trades in day_trades.items():
                family_trades[family].extend(trades)
    finally:
        conn.close()

    v3_dates: set[str] = set()
    if include_v3_overlap and usable_dates:
        validation = build_v3_validation(
            db_path,
            sessions=sessions,
            source=source,
        )
        v3_dates = set(
            validation["variants"]["guard_d2_m2_slope_0_015"]["triggered_dates"]
        )

    folds = _folds(usable_dates)
    family_reports: dict[str, Any] = {}
    for family in FAMILIES:
        trades = sorted(family_trades[family], key=lambda item: item.entry_time)
        metrics = _metrics(trades, usable_sessions=len(usable_dates))
        triggered = len(trades)
        setup_count = int(family_setup_counts[family])
        non_v3 = [trade for trade in trades if trade.day not in v3_dates]
        metrics["setups"] = setup_count
        metrics["trigger_rate_pct"] = round(100 * triggered / setup_count, 2) if setup_count else 0.0
        metrics["chronological_folds"] = [
            {
                "fold": index + 1,
                "test_start": test_dates[0],
                "test_end": test_dates[-1],
                "metrics": _period_metrics([
                    trade for trade in trades if trade.day in set(test_dates)
                ]),
            }
            for index, test_dates in enumerate(folds)
        ]
        metrics["strategy_a_v3_same_day_overlap"] = {
            "v3_signal_days": len(v3_dates),
            "trades_on_v3_signal_days": sum(trade.day in v3_dates for trade in trades),
            "trades_on_non_v3_days": len(non_v3),
            "non_v3_day_contribution": _period_metrics(non_v3),
            "note": "Overlap is day-level, not exact simultaneous-position overlap.",
        }
        family_reports[family] = {
            "metrics": metrics,
            "trades": [trade.to_dict() for trade in trades],
        }

    return {
        "research_type": "STRATEGY_CD_NATIVE_15M_5M_1M_DISCOVERY",
        "source": source.upper(),
        "db_path": str(db_path.resolve()),
        "sessions_requested": sessions,
        "usable_sessions": len(usable_dates),
        "usable_dates": usable_dates,
        "skipped_sessions": skipped,
        "config": asdict(cfg),
        "timeframe_contract": {
            "context": "completed 15m aggregated only from completed native 5m bars",
            "setup": "completed native 5m",
            "trigger": "completed native 1m beginning only after setup close",
            "lifecycle": "native 1m; stop-first same-bar ambiguity; trail changes effective next bar",
        },
        "families": family_reports,
        "limitations": [
            "Underlying futures R only; not executable historical option P&L.",
            "Candidate thresholds are discovery hypotheses, not production settings.",
            "Strategy A V3 remains unchanged.",
            "Strategy A overlap is measured at signal-day granularity in this first pass.",
            "Production lifecycle, option execution, conflict rules, paper/shadow, and forward validation are still required before deployment.",
        ],
        "production_thresholds_changed": False,
        "market_data_written": False,
        "broker_called": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Native 15m/5m/1m Strategy C/D discovery")
    parser.add_argument("--db-path", type=Path, default=_default_db_path())
    parser.add_argument("--sessions", type=int, default=0, help="0 means all cached sessions")
    parser.add_argument("--source", choices=("BREEZE", "KITE", "LIVE", "MIXED"), default="BREEZE")
    parser.add_argument("--skip-v3-overlap", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data") / "strategy_cd_research" / "strategy_cd_multitimeframe_discovery.json",
    )
    args = parser.parse_args()
    report = build_report(
        args.db_path,
        source=args.source,
        sessions=args.sessions,
        include_v3_overlap=not args.skip_v3_overlap,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    compact = {
        "research_type": report["research_type"],
        "usable_sessions": report["usable_sessions"],
        "skipped_sessions": len(report["skipped_sessions"]),
        "families": {
            name: payload["metrics"]
            for name, payload in report["families"].items()
        },
        "output": str(args.output),
        "production_thresholds_changed": False,
    }
    print(json.dumps(compact, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
