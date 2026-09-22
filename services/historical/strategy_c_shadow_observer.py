"""Live/shadow replay adapter for frozen Strategy C candidate V1.

This module replays the raw DI-continuation family only up to an explicit
as_of timestamp, then labels raw entries that satisfy the frozen Strategy C
candidate guard. Replaying the raw family is intentional: the historical
candidate was selected from raw DI trades after the raw family's lifecycle,
max-two-trades/day rule, and overlap blocking had already been applied.

The module is research-only. It does not select options, place orders, write
market data, or change Strategy A/B configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

from libs.contracts.models import Candle
from services.historical.strategy_a_data_audit import IST
from services.historical.strategy_c_candidate_manifest import (
    CANDIDATE_ID,
    _signal_id,
    matches_candidate,
)
from services.historical.strategy_cd_multitimeframe_discovery import (
    DiscoveryConfig,
    FORCED_EXIT,
    MTFFeature,
    Trigger,
    _feature_map_for_ends,
    _find_trigger,
    _in_setup_window,
    _setup_matches,
)
from services.strategy.futures_signal import (
    FuturesFeatureEngine,
    aggregate_completed_15m,
    canonical_active_futures_stream,
)


RAW_FAMILY = "di_continuation"


@dataclass(frozen=True)
class ShadowLifecycle:
    status: str
    exit_time: datetime | None
    exit_price: float | None
    exit_reason: str | None
    realized_r: float | None
    current_stop: float
    current_r: float
    mfe_r: float
    mae_r: float


def _run_lifecycle_to_as_of(
    trigger: Trigger,
    one_minute: Sequence[Candle],
    config: DiscoveryConfig,
    *,
    as_of: datetime,
) -> ShadowLifecycle:
    """Replay the frozen underlying lifecycle without inventing a future exit."""
    entry = trigger.entry_price
    initial_stop = trigger.initial_stop
    risk = abs(entry - initial_stop)
    if risk <= 0:
        raise ValueError("Strategy C trigger risk must be positive")

    direction = trigger.direction
    session_day = trigger.entry_bar.end_time.astimezone(IST).date()
    cutoff = datetime.combine(session_day, FORCED_EXIT, tzinfo=IST)
    horizon = min(as_of.astimezone(IST), cutoff).astimezone(as_of.tzinfo)

    post = [
        bar
        for bar in one_minute
        if bar.start_time >= trigger.entry_bar.end_time
        and bar.end_time <= horizon
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
    latest_price = entry

    for bar in post:
        latest_price = bar.close
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

        if stop_hit:
            realized = (
                (active_stop - entry) / risk
                if direction == "CALL"
                else (entry - active_stop) / risk
            )
            return ShadowLifecycle(
                status="RESOLVED",
                exit_time=bar.end_time,
                exit_price=active_stop,
                exit_reason="STOP_OR_TRAIL",
                realized_r=realized,
                current_stop=active_stop,
                current_r=realized,
                mfe_r=mfe,
                mae_r=mae,
            )
        if target_hit:
            return ShadowLifecycle(
                status="RESOLVED",
                exit_time=bar.end_time,
                exit_price=target,
                exit_reason="HARD_TARGET",
                realized_r=config.hard_target_r,
                current_stop=active_stop,
                current_r=config.hard_target_r,
                mfe_r=mfe,
                mae_r=mae,
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

    if as_of.astimezone(IST) >= cutoff:
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
        return ShadowLifecycle(
            status="RESOLVED",
            exit_time=exit_time,
            exit_price=exit_price,
            exit_reason="FORCED_EXIT",
            realized_r=realized,
            current_stop=active_stop,
            current_r=realized,
            mfe_r=mfe,
            mae_r=mae,
        )

    current_r = (
        (latest_price - entry) / risk
        if direction == "CALL"
        else (entry - latest_price) / risk
    )
    return ShadowLifecycle(
        status="OPEN",
        exit_time=None,
        exit_price=None,
        exit_reason=None,
        realized_r=None,
        current_stop=active_stop,
        current_r=current_r,
        mfe_r=mfe,
        mae_r=mae,
    )


def _context_features(
    stream_15m: Sequence[Candle],
    context_ends: set[datetime],
) -> dict[datetime, MTFFeature]:
    result: dict[datetime, MTFFeature] = {}
    history: list[Candle] = []
    for bar in stream_15m:
        history.append(bar)
        if bar.end_time not in context_ends:
            continue
        snap = FuturesFeatureEngine.build(history, as_of=bar.end_time)
        result[bar.end_time] = MTFFeature(
            candle=bar,
            ema20=snap.ema20,
            ema50=snap.ema50,
            adx14=snap.adx14,
            plus_di14=snap.plus_di14,
            minus_di14=snap.minus_di14,
            atr14=snap.atr14,
            session_vwap=snap.session_vwap,
        )
    return result


def _entry_dict(trigger: Trigger) -> dict[str, Any]:
    return {
        "family": trigger.family,
        "direction": trigger.direction,
        "date": trigger.entry_bar.end_time.astimezone(IST).date().isoformat(),
        "setup_end": trigger.setup_end.isoformat(),
        "entry_time": trigger.entry_bar.end_time.isoformat(),
        "entry_time_ist": trigger.entry_bar.end_time.astimezone(IST).isoformat(),
        "entry_price": trigger.entry_price,
        "initial_stop": trigger.initial_stop,
        "research_features": dict(trigger.research_features),
    }


def replay_strategy_c_to_as_of(
    raw_5m: Sequence[Candle],
    raw_1m: Sequence[Candle],
    *,
    as_of: datetime,
    config: DiscoveryConfig | None = None,
) -> dict[str, Any]:
    """Replay today's raw DI family through as_of and expose frozen C rows."""
    cfg = config or DiscoveryConfig()
    stream_5m = canonical_active_futures_stream(raw_5m, as_of=as_of, interval="5m")
    stream_15m = canonical_active_futures_stream(
        aggregate_completed_15m(raw_5m, as_of=as_of),
        as_of=as_of,
        interval="15m",
    )
    stream_1m = canonical_active_futures_stream(raw_1m, as_of=as_of, interval="1m")
    day = as_of.astimezone(IST).date()

    setup_bars = [
        bar
        for bar in stream_5m
        if bar.end_time.astimezone(IST).date() == day
        and _in_setup_window(bar.end_time)
    ]
    if not setup_bars:
        return {
            "status": "NO_5M_SETUP_WINDOW",
            "raw_entries": [],
            "candidate_entries": [],
            "active_raw_trade": None,
        }

    first_setup = setup_bars[0].end_time
    warmup_5m = sum(bar.end_time <= first_setup for bar in stream_5m)
    warmup_15m = sum(bar.end_time <= first_setup for bar in stream_15m)
    if warmup_5m < 50 or warmup_15m < 50:
        return {
            "status": f"INSUFFICIENT_WARMUP:5m={warmup_5m},15m={warmup_15m}",
            "raw_entries": [],
            "candidate_entries": [],
            "active_raw_trade": None,
        }

    index_5m = {bar.end_time: index for index, bar in enumerate(stream_5m)}
    setup_ends = {bar.end_time for bar in setup_bars}
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
    features_15m = _context_features(stream_15m, context_ends)

    raw_entries: list[dict[str, Any]] = []
    candidate_entries: list[dict[str, Any]] = []
    active_raw_trade: dict[str, Any] | None = None
    blocked_until: datetime | None = None

    for setup in setup_bars:
        if len(raw_entries) >= cfg.max_trades_per_day:
            break
        idx = index_5m[setup.end_time]
        if idx == 0:
            continue
        current = features_5m.get(setup.end_time)
        previous = features_5m.get(stream_5m[idx - 1].end_time)
        context_end = context_end_for_setup.get(setup.end_time)
        context = features_15m.get(context_end) if context_end is not None else None
        if current is None or previous is None or context is None:
            continue

        for direction in ("CALL", "PUT"):
            if not _setup_matches(RAW_FAMILY, context, current, previous, direction):
                continue
            trigger = _find_trigger(
                family=RAW_FAMILY,
                direction=direction,
                current=current,
                previous=previous,
                one_minute=stream_1m,
                config=cfg,
                context=context,
            )
            if trigger is None:
                continue
            if blocked_until is not None and trigger.entry_bar.end_time < blocked_until:
                continue

            entry = _entry_dict(trigger)
            lifecycle = _run_lifecycle_to_as_of(
                trigger,
                stream_1m,
                cfg,
                as_of=as_of,
            )
            candidate = matches_candidate(entry)
            row = {
                **entry,
                "candidate_match": candidate,
                "candidate_signal_id": _signal_id(entry) if candidate else None,
                "lifecycle": {
                    "status": lifecycle.status,
                    "exit_time": lifecycle.exit_time.isoformat() if lifecycle.exit_time else None,
                    "exit_price": lifecycle.exit_price,
                    "exit_reason": lifecycle.exit_reason,
                    "realized_r": lifecycle.realized_r,
                    "current_stop": lifecycle.current_stop,
                    "current_r": lifecycle.current_r,
                    "mfe_r": lifecycle.mfe_r,
                    "mae_r": lifecycle.mae_r,
                },
            }
            raw_entries.append(row)
            if candidate:
                candidate_entries.append(row)

            if lifecycle.status == "OPEN":
                active_raw_trade = row
                return {
                    "status": "ACTIVE_RAW_DI_TRADE",
                    "raw_entries": raw_entries,
                    "candidate_entries": candidate_entries,
                    "active_raw_trade": active_raw_trade,
                }

            blocked_until = lifecycle.exit_time
            break

    return {
        "status": "OK",
        "raw_entries": raw_entries,
        "candidate_entries": candidate_entries,
        "active_raw_trade": active_raw_trade,
    }


def candidate_id() -> str:
    return CANDIDATE_ID
