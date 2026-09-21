"""Read-only historical research harness for Strategy A V2.

The production Strategy A configuration is treated as a frozen baseline
hypothesis.  This module does not alter thresholds, write market data, persist
trades, call a broker, or claim option profitability.

It builds one directional research row for every completed 15-minute futures
decision bar, labels the subsequent underlying path without look-ahead in the
decision features, and produces:
- candidate-level feature/outcome evidence,
- one-rule-at-a-time ablations,
- one-parameter-at-a-time robustness sweeps,
- chronological walk-forward selection/evaluation.

The scalar research score is deliberately *not* production realized R.  It is
"T1-first-hit R": -1R when the structural stop is reached before T1, +T1_R
when T1 is reached before the structural stop, otherwise the session-exit R
clipped to [-1R, +T1_R].  It is an entry-quality research label intended for
comparing filters while keeping the production exit policy out of the fitting
loop.  Actual production lifecycle replay remains a separate validation step.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Sequence

from libs.contracts.models import Candle
from services.historical.strategy_a_data_audit import (
    ENTRY_FIRST_END,
    ENTRY_LAST_END,
    IST,
    OPTIONAL_CLOSE_MARKER,
    SESSION_START,
    WARMUP_CALENDAR_DAYS,
    _default_db_path,
    _latest_spot_session_dates,
    _load_rows,
    _open_read_only,
)
from services.strategy.futures_signal import (
    FuturesFeatureEngine,
    FuturesFeatureSnapshot,
    aggregate_completed_15m,
    canonical_active_futures_stream,
)
from services.strategy.models import StrategyDirection, StrategyTunablesConfig


FORCED_EXIT = time(15, 15)
EPSILON = 1e-9


@dataclass(frozen=True)
class ResearchVariant:
    name: str
    parameter_overrides: dict[str, float]
    ignored_rules: frozenset[str] = frozenset()
    description: str = ""


def _session_dates(conn: Any, *, sessions: int, source: str) -> list[date]:
    """Return chronological cached sessions; sessions=0 means all available."""
    requested = sessions if sessions > 0 else 10000
    dates = _latest_spot_session_dates(
        conn,
        sessions=requested,
        source=source,
    )
    return sorted(dates)


def _decision_end(day: date, value: time) -> datetime:
    return datetime.combine(day, value, tzinfo=IST)


def _in_decision_window(value: datetime) -> bool:
    local = value.astimezone(IST)
    first = _decision_end(local.date(), ENTRY_FIRST_END)
    last = _decision_end(local.date(), ENTRY_LAST_END)
    normalized = local.replace(second=0, microsecond=0)
    return first <= normalized <= last and normalized.minute % 15 == 0


def _load_canonical_stream(
    conn: Any,
    day: date,
    *,
    source: str,
) -> list[Candle]:
    warmup_start = datetime.combine(
        day - timedelta(days=WARMUP_CALENDAR_DAYS),
        SESSION_START,
        tzinfo=IST,
    )
    history_end = datetime.combine(
        day,
        OPTIONAL_CLOSE_MARKER,
        tzinfo=IST,
    ) + timedelta(minutes=5)
    rows_5m = _load_rows(
        conn,
        start_utc=warmup_start.astimezone(timezone.utc),
        end_utc=history_end.astimezone(timezone.utc),
        source=source,
        instrument_like="INST-NIFTY-FUT-%",
    )
    bars_15m = aggregate_completed_15m(rows_5m, as_of=history_end)
    return canonical_active_futures_stream(
        bars_15m,
        as_of=history_end,
        interval="15m",
    )


def _feature_map(stream: Sequence[Candle]) -> dict[datetime, FuturesFeatureSnapshot]:
    """Build each feature snapshot from information available at that bar only."""
    result: dict[datetime, FuturesFeatureSnapshot] = {}
    history: list[Candle] = []
    for bar in stream:
        history.append(bar)
        result[bar.end_time] = FuturesFeatureEngine.build(
            history,
            as_of=bar.end_time,
        )
    return result


def _distance_to_bar_atr(level: float | None, feature: FuturesFeatureSnapshot) -> float | None:
    if level is None or feature.atr14 <= 0:
        return None
    if feature.low <= level <= feature.high:
        return 0.0
    distance = min(
        abs(level - feature.low),
        abs(level - feature.high),
        abs(level - feature.close),
    )
    return distance / feature.atr14


def _point_distance_atr(level: float, feature: FuturesFeatureSnapshot) -> float | None:
    if feature.atr14 <= 0:
        return None
    return min(
        abs(level - feature.low),
        abs(level - feature.high),
        abs(level - feature.close),
    ) / feature.atr14


def _geometry(
    feature: FuturesFeatureSnapshot,
    direction: StrategyDirection,
    config: StrategyTunablesConfig,
) -> dict[str, float | None]:
    atr = feature.atr14
    if atr <= 0:
        return {
            "trigger": None,
            "stop": None,
            "risk_points": None,
            "risk_atr": None,
            "relevant_sr": None,
            "opposing_sr": None,
            "opposing_room_r": None,
        }

    if direction is StrategyDirection.CALL:
        relevant = feature.support
        trigger = feature.high + config.trigger_buffer_atr * atr
        structural_extreme = (
            min(feature.low, feature.support)
            if feature.support is not None
            else feature.low
        )
        stop = structural_extreme - config.structural_stop_buffer_atr * atr
        opposing = (
            feature.resistance
            if feature.resistance is not None and feature.resistance > trigger
            else None
        )
    else:
        relevant = feature.resistance
        trigger = feature.low - config.trigger_buffer_atr * atr
        structural_extreme = (
            max(feature.high, feature.resistance)
            if feature.resistance is not None
            else feature.high
        )
        stop = structural_extreme + config.structural_stop_buffer_atr * atr
        opposing = (
            feature.support
            if feature.support is not None and feature.support < trigger
            else None
        )

    risk = abs(trigger - stop)
    risk_atr = risk / atr if atr > 0 else None
    opposing_room = (
        abs(opposing - trigger) / risk
        if opposing is not None and risk > 0
        else None
    )
    return {
        "trigger": trigger,
        "stop": stop,
        "risk_points": risk,
        "risk_atr": risk_atr,
        "relevant_sr": relevant,
        "opposing_sr": opposing,
        "opposing_room_r": opposing_room,
    }


def _base_row(
    day: date,
    feature: FuturesFeatureSnapshot,
    direction: StrategyDirection,
    config: StrategyTunablesConfig,
) -> dict[str, Any]:
    range_points = max(0.0, feature.high - feature.low)
    body_ratio = (
        abs(feature.close - feature.open) / range_points
        if range_points > 0
        else 0.0
    )
    range_atr = (
        range_points / feature.atr14
        if feature.atr14 > 0
        else None
    )
    ema_separation_atr = (
        abs(feature.ema20 - feature.ema50) / feature.atr14
        if feature.atr14 > 0
        else None
    )

    if direction is StrategyDirection.CALL:
        ema_order = feature.ema20 > feature.ema50
        di_direction = feature.plus_di14 > feature.minus_di14
        candle_direction = feature.close > feature.open
        close_location_pct = (
            (feature.high - feature.close) / range_points
            if range_points > 0
            else None
        )
    else:
        ema_order = feature.ema20 < feature.ema50
        di_direction = feature.minus_di14 > feature.plus_di14
        candle_direction = feature.close < feature.open
        close_location_pct = (
            (feature.close - feature.low) / range_points
            if range_points > 0
            else None
        )

    geometry = _geometry(feature, direction, config)
    relevant_sr = geometry["relevant_sr"]

    row: dict[str, Any] = {
        "date": day.isoformat(),
        "timestamp": feature.candle_timestamp.isoformat(),
        "timestamp_ist": feature.candle_timestamp.astimezone(IST).isoformat(),
        "direction": direction.value,
        "contract": feature.contract_id,
        "bar_index": feature.bar_index,
        "open": feature.open,
        "high": feature.high,
        "low": feature.low,
        "close": feature.close,
        "ema20": feature.ema20,
        "ema50": feature.ema50,
        "adx14": feature.adx14,
        "plus_di14": feature.plus_di14,
        "minus_di14": feature.minus_di14,
        "atr14": feature.atr14,
        "session_vwap": feature.session_vwap,
        "support": feature.support,
        "resistance": feature.resistance,
        "ema_separation_atr": ema_separation_atr,
        "confirmation_body_ratio": body_ratio,
        "confirmation_range_atr": range_atr,
        "confirmation_close_location_pct": close_location_pct,
        "sr_present": relevant_sr is not None,
        "sr_touch_distance_atr": _distance_to_bar_atr(relevant_sr, feature),
        "ema_distance_atr": _point_distance_atr(feature.ema20, feature),
        "vwap_distance_atr": _point_distance_atr(feature.session_vwap, feature),
        "ema_order_directional": ema_order,
        "di_directional": di_direction,
        "confirmation_directional": candle_direction,
        **geometry,
    }
    row["baseline_pass"] = _row_passes(
        row,
        config=config,
        ignored_rules=frozenset(),
    )
    row["baseline_components"] = _component_results(row, config=config)
    return row


def _value(config: StrategyTunablesConfig, overrides: dict[str, float], name: str) -> float:
    return float(overrides.get(name, getattr(config, name)))


def _component_results(
    row: dict[str, Any],
    *,
    config: StrategyTunablesConfig,
    overrides: dict[str, float] | None = None,
) -> dict[str, bool]:
    o = overrides or {}
    sr_distance = row.get("sr_touch_distance_atr")
    ema_distance = row.get("ema_distance_atr")
    vwap_distance = row.get("vwap_distance_atr")
    risk_atr = row.get("risk_atr")
    opposing_room = row.get("opposing_room_r")

    results = {
        "ema_order": bool(row.get("ema_order_directional")),
        "di_direction": bool(row.get("di_directional")),
        "adx": float(row.get("adx14") or 0.0) + EPSILON >= _value(config, o, "adx_threshold"),
        "ema_separation": (
            row.get("ema_separation_atr") is not None
            and float(row["ema_separation_atr"]) + EPSILON
            >= _value(config, o, "ema_separation_min_atr")
        ),
        "confirmation_direction": bool(row.get("confirmation_directional")),
        "confirmation_body": (
            float(row.get("confirmation_body_ratio") or 0.0) + EPSILON
            >= _value(config, o, "confirmation_min_body_ratio")
        ),
        "confirmation_close_location": (
            row.get("confirmation_close_location_pct") is not None
            and float(row["confirmation_close_location_pct"])
            <= _value(config, o, "confirmation_close_location_pct") + EPSILON
        ),
        "confirmation_range": (
            row.get("confirmation_range_atr") is not None
            and float(row["confirmation_range_atr"])
            <= _value(config, o, "confirmation_max_range_atr") + EPSILON
        ),
        "sr_present": bool(row.get("sr_present")),
        "sr_touch": (
            sr_distance is not None
            and float(sr_distance) <= _value(config, o, "sr_zone_atr") + EPSILON
        ),
        "ema_or_vwap_near": (
            (
                ema_distance is not None
                and float(ema_distance)
                <= _value(config, o, "confluence_distance_atr") + EPSILON
            )
            or (
                vwap_distance is not None
                and float(vwap_distance)
                <= _value(config, o, "confluence_distance_atr") + EPSILON
            )
        ),
        "minimum_stop_distance": (
            risk_atr is not None
            and float(risk_atr) + EPSILON
            >= _value(config, o, "minimum_stop_distance_atr")
        ),
        "maximum_stop_distance": (
            risk_atr is not None
            and float(risk_atr)
            <= _value(config, o, "maximum_stop_distance_atr") + EPSILON
        ),
        "opposing_sr_room": (
            opposing_room is None
            or float(opposing_room) + EPSILON
            >= _value(config, o, "minimum_room_to_opposing_sr_r")
        ),
    }
    return results


REQUIRED_RULES = (
    "ema_order",
    "di_direction",
    "adx",
    "ema_separation",
    "confirmation_direction",
    "confirmation_body",
    "confirmation_close_location",
    "confirmation_range",
    "sr_present",
    "sr_touch",
    "ema_or_vwap_near",
    "minimum_stop_distance",
    "maximum_stop_distance",
    "opposing_sr_room",
)


def _row_passes(
    row: dict[str, Any],
    *,
    config: StrategyTunablesConfig,
    parameter_overrides: dict[str, float] | None = None,
    ignored_rules: frozenset[str] = frozenset(),
) -> bool:
    components = _component_results(
        row,
        config=config,
        overrides=parameter_overrides,
    )
    return all(
        components[rule]
        for rule in REQUIRED_RULES
        if rule not in ignored_rules
    )


def _trigger_label(
    *,
    row: dict[str, Any],
    future_bars: Sequence[Candle],
    future_features: dict[datetime, FuturesFeatureSnapshot],
    config: StrategyTunablesConfig,
) -> dict[str, Any]:
    trigger = row.get("trigger")
    stop = row.get("stop")
    base_risk = row.get("risk_points")
    if trigger is None or stop is None or base_risk is None or base_risk <= 0:
        return {
            "trigger_status": "UNLABELABLE",
            "trigger_timestamp": None,
            "entry_price": None,
            "entry_risk_points": None,
            "max_favorable_r": None,
            "max_adverse_r": None,
            "hit_1r_before_stop": None,
            "hit_t1_before_stop": None,
            "hit_runner_before_stop": None,
            "t1_first_hit_r": None,
            "session_exit_r": None,
            "post_entry_bars": 0,
        }

    direction = StrategyDirection(row["direction"])
    validity = list(future_bars[: config.trigger_validity_bars])
    entry_bar: Candle | None = None
    entry_price: float | None = None
    trigger_status = "EXPIRED"

    for bar in validity:
        if direction is StrategyDirection.CALL and bar.close <= float(stop):
            trigger_status = "INVALIDATED_BEFORE_TRIGGER"
            break
        if direction is StrategyDirection.PUT and bar.close >= float(stop):
            trigger_status = "INVALIDATED_BEFORE_TRIGGER"
            break

        gap_triggered = (
            bar.open >= float(trigger)
            if direction is StrategyDirection.CALL
            else bar.open <= float(trigger)
        )
        touched = (
            bar.high >= float(trigger)
            if direction is StrategyDirection.CALL
            else bar.low <= float(trigger)
        )
        if not (gap_triggered or touched):
            continue

        local_trigger_time = bar.end_time.astimezone(IST)
        start_h, start_m = map(int, config.entry_session_start.split(":"))
        end_h, end_m = map(int, config.entry_session_end.split(":"))
        trigger_minutes = local_trigger_time.hour * 60 + local_trigger_time.minute
        if not (
            start_h * 60 + start_m
            <= trigger_minutes
            <= end_h * 60 + end_m
        ):
            trigger_status = "ENTRY_SESSION_CLOSED"
            break

        candidate_entry = bar.open if gap_triggered else float(trigger)
        chase = (
            candidate_entry - float(trigger)
            if direction is StrategyDirection.CALL
            else float(trigger) - candidate_entry
        )
        bar_feature = future_features.get(bar.end_time)
        chase_atr = bar_feature.atr14 if bar_feature is not None else float(row.get("atr14") or 0.0)
        if chase > config.maximum_chase_atr * chase_atr + EPSILON:
            trigger_status = "CHASE_REJECTED"
            break

        entry_bar = bar
        entry_price = candidate_entry
        trigger_status = "TRIGGERED"
        break

    if entry_bar is None or entry_price is None:
        return {
            "trigger_status": trigger_status,
            "trigger_timestamp": None,
            "entry_price": None,
            "entry_risk_points": None,
            "max_favorable_r": None,
            "max_adverse_r": None,
            "hit_1r_before_stop": None,
            "hit_t1_before_stop": None,
            "hit_runner_before_stop": None,
            "t1_first_hit_r": None,
            "session_exit_r": None,
            "post_entry_bars": 0,
        }

    risk = abs(entry_price - float(stop))
    if risk <= 0:
        return {
            "trigger_status": "UNLABELABLE",
            "trigger_timestamp": entry_bar.end_time.isoformat(),
            "entry_price": entry_price,
            "entry_risk_points": risk,
            "max_favorable_r": None,
            "max_adverse_r": None,
            "hit_1r_before_stop": None,
            "hit_t1_before_stop": None,
            "hit_runner_before_stop": None,
            "t1_first_hit_r": None,
            "session_exit_r": None,
            "post_entry_bars": 0,
        }

    session_day = entry_bar.end_time.astimezone(IST).date()
    cutoff = datetime.combine(session_day, FORCED_EXIT, tzinfo=IST)
    post = [
        bar
        for bar in future_bars
        if bar.end_time > entry_bar.end_time
        and bar.end_time.astimezone(IST) <= cutoff
    ]

    max_favorable = 0.0
    max_adverse = 0.0
    hit_1r = False
    hit_t1 = False
    hit_runner = False
    stop_before_t1 = False

    for bar in post:
        if direction is StrategyDirection.CALL:
            favorable_r = (bar.high - entry_price) / risk
            adverse_r = (entry_price - bar.low) / risk
            stop_hit = bar.low <= float(stop)
            one_hit = bar.high >= entry_price + risk
            t1_hit = bar.high >= entry_price + config.t1_r * risk
            runner_hit = bar.high >= entry_price + config.runner_target_reference_r * risk
        else:
            favorable_r = (entry_price - bar.low) / risk
            adverse_r = (bar.high - entry_price) / risk
            stop_hit = bar.high >= float(stop)
            one_hit = bar.low <= entry_price - risk
            t1_hit = bar.low <= entry_price - config.t1_r * risk
            runner_hit = bar.low <= entry_price - config.runner_target_reference_r * risk

        max_favorable = max(max_favorable, favorable_r)
        max_adverse = max(max_adverse, adverse_r)

        # Conservative ordering for OHLC ambiguity: protective stop wins when
        # the same 15m bar contains both stop and favorable target.
        if stop_hit:
            if not hit_t1:
                stop_before_t1 = True
            break
        hit_1r = hit_1r or one_hit
        hit_t1 = hit_t1 or t1_hit
        hit_runner = hit_runner or runner_hit
        if hit_runner:
            break

    last_close = post[-1].close if post else entry_bar.close
    if direction is StrategyDirection.CALL:
        session_exit_r = (last_close - entry_price) / risk
    else:
        session_exit_r = (entry_price - last_close) / risk

    if hit_t1:
        first_hit_r = config.t1_r
    elif stop_before_t1:
        first_hit_r = -1.0
    else:
        first_hit_r = max(-1.0, min(config.t1_r, session_exit_r))

    return {
        "trigger_status": trigger_status,
        "trigger_timestamp": entry_bar.end_time.isoformat(),
        "entry_price": entry_price,
        "entry_risk_points": risk,
        "max_favorable_r": round(max_favorable, 6),
        "max_adverse_r": round(max_adverse, 6),
        "hit_1r_before_stop": hit_1r,
        "hit_t1_before_stop": hit_t1,
        "hit_runner_before_stop": hit_runner,
        "t1_first_hit_r": round(first_hit_r, 6),
        "session_exit_r": round(session_exit_r, 6),
        "post_entry_bars": len(post),
    }


def build_candidates(
    db_path: Path,
    *,
    sessions: int = 0,
    source: str = "BREEZE",
) -> dict[str, Any]:
    config = StrategyTunablesConfig()
    conn = _open_read_only(db_path)
    rows: list[dict[str, Any]] = []
    skipped_sessions: list[dict[str, str]] = []
    try:
        dates = _session_dates(conn, sessions=sessions, source=source)
        for day in dates:
            stream = _load_canonical_stream(conn, day, source=source)
            if not stream:
                skipped_sessions.append({
                    "date": day.isoformat(),
                    "reason": "NO_CANONICAL_FUTURES_STREAM",
                })
                continue
            first_decision = datetime.combine(day, ENTRY_FIRST_END, tzinfo=IST)
            warmup_count = sum(
                bar.end_time.astimezone(IST) <= first_decision
                for bar in stream
            )
            if warmup_count < 50:
                skipped_sessions.append({
                    "date": day.isoformat(),
                    "reason": "INSUFFICIENT_50_BAR_FEATURE_WARMUP",
                })
                continue
            features = _feature_map(stream)
            session_bars = [
                bar for bar in stream
                if bar.end_time.astimezone(IST).date() == day
            ]
            decision_bars = [
                bar for bar in session_bars
                if _in_decision_window(bar.end_time)
            ]
            if len(decision_bars) != 21:
                skipped_sessions.append({
                    "date": day.isoformat(),
                    "reason": f"INCOMPLETE_DECISION_WINDOW:{len(decision_bars)}/21",
                })
                continue
            for bar in decision_bars:
                feature = features[bar.end_time]
                future = [
                    item
                    for item in session_bars
                    if item.end_time > bar.end_time
                ]
                for direction in (StrategyDirection.CALL, StrategyDirection.PUT):
                    row = _base_row(day, feature, direction, config)
                    row["label"] = _trigger_label(
                        row=row,
                        future_bars=future,
                        future_features=features,
                        config=config,
                    )
                    rows.append(row)
    finally:
        conn.close()

    rows.sort(key=lambda row: (row["timestamp"], row["direction"]))
    return {
        "research_type": "STRATEGY_A_V2_DIRECTIONAL_CANDIDATE_LABELS",
        "source": source.upper(),
        "db_path": str(db_path.resolve()),
        "sessions_requested": sessions,
        "sessions_found": len({row["date"] for row in rows}),
        "directional_rows": len(rows),
        "baseline_thresholds": _research_thresholds(config),
        "label_definition": {
            "trigger_validity_bars": config.trigger_validity_bars,
            "maximum_chase_atr": config.maximum_chase_atr,
            "forced_exit_time": config.forced_exit_time,
            "t1_r": config.t1_r,
            "runner_reference_r": config.runner_target_reference_r,
            "same_bar_ambiguity_policy": "STOP_FIRST",
            "post_entry_measurement_starts": "AFTER_TRIGGER_BAR",
            "t1_first_hit_r": (
                "+T1_R when T1 occurs before structural stop; -1R when stop "
                "occurs before T1; otherwise session-exit R clipped to that range"
            ),
        },
        "limitations": [
            "Research labels use underlying futures only; they are not option PnL.",
            "15m OHLC cannot establish intrabar event order; same-bar stop/target ambiguity is resolved stop-first.",
            "Each decision row is an independent counterfactual opportunity, so overlapping rows are not portfolio trades.",
            "Production lifecycle replay and paper/shadow validation remain required after rule selection.",
        ],
        "skipped_sessions": skipped_sessions,
        "rows": rows,
    }


def _max_drawdown(values: Sequence[float]) -> float | None:
    if not values:
        return None
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return round(drawdown, 6)


def _selected_rows(
    rows: Iterable[dict[str, Any]],
    variant: ResearchVariant,
    config: StrategyTunablesConfig,
) -> list[dict[str, Any]]:
    return [
        row
        for row in rows
        if _row_passes(
            row,
            config=config,
            parameter_overrides=variant.parameter_overrides,
            ignored_rules=variant.ignored_rules,
        )
    ]


def _summarize_rows(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    selected = list(rows)
    triggered = [
        row for row in selected
        if (row.get("label") or {}).get("trigger_status") == "TRIGGERED"
    ]
    scores = [
        float(row["label"]["t1_first_hit_r"])
        for row in triggered
        if row.get("label", {}).get("t1_first_hit_r") is not None
    ]
    mfe = [
        float(row["label"]["max_favorable_r"])
        for row in triggered
        if row.get("label", {}).get("max_favorable_r") is not None
    ]
    mae = [
        float(row["label"]["max_adverse_r"])
        for row in triggered
        if row.get("label", {}).get("max_adverse_r") is not None
    ]
    t1 = sum(bool(row["label"].get("hit_t1_before_stop")) for row in triggered)
    runner = sum(bool(row["label"].get("hit_runner_before_stop")) for row in triggered)
    by_direction = Counter(row["direction"] for row in triggered)
    months: dict[str, list[float]] = {}
    for row in triggered:
        score = row["label"].get("t1_first_hit_r")
        if score is None:
            continue
        months.setdefault(row["date"][:7], []).append(float(score))

    return {
        "selected_rows": len(selected),
        "triggered_rows": len(triggered),
        "trigger_rate_pct": round(len(triggered) / len(selected) * 100, 2) if selected else 0.0,
        "t1_before_stop": t1,
        "t1_before_stop_pct": round(t1 / len(triggered) * 100, 2) if triggered else 0.0,
        "runner_before_stop": runner,
        "runner_before_stop_pct": round(runner / len(triggered) * 100, 2) if triggered else 0.0,
        "mean_t1_first_hit_r": round(mean(scores), 6) if scores else None,
        "sum_t1_first_hit_r": round(sum(scores), 6) if scores else 0.0,
        "max_drawdown_t1_first_hit_r": _max_drawdown(scores),
        "mean_mfe_r": round(mean(mfe), 6) if mfe else None,
        "mean_mae_r": round(mean(mae), 6) if mae else None,
        "direction_distribution": dict(sorted(by_direction.items())),
        "profitable_months": sum(
            sum(values) > 0 for values in months.values()
        ),
        "months_with_triggered_rows": len(months),
        "monthly_t1_first_hit_r": {
            month: {
                "count": len(values),
                "sum_r": round(sum(values), 6),
                "mean_r": round(mean(values), 6),
            }
            for month, values in sorted(months.items())
        },
    }


def _ablation_variants() -> list[ResearchVariant]:
    specs = [
        ("baseline", None, "All Strategy A V2 entry gates."),
        ("without_adx", "adx", "Remove only the ADX threshold."),
        ("without_ema_separation", "ema_separation", "Remove only minimum EMA separation."),
        ("without_confirmation_body", "confirmation_body", "Remove only confirmation body-ratio threshold."),
        ("without_confirmation_close_location", "confirmation_close_location", "Remove only directional close-location threshold."),
        ("without_confirmation_range_cap", "confirmation_range", "Remove only confirmation range/ATR cap."),
        ("without_sr_touch", "sr_touch", "Keep confirmed S/R but remove bar-touch zone requirement."),
        ("without_ema_vwap_proximity", "ema_or_vwap_near", "Keep S/R touch but remove EMA20/VWAP proximity requirement."),
        ("without_min_stop_distance", "minimum_stop_distance", "Remove only minimum structural stop distance."),
        ("without_max_stop_distance", "maximum_stop_distance", "Remove only maximum structural stop distance."),
        ("without_opposing_sr_room", "opposing_sr_room", "Remove only minimum room to opposing S/R."),
    ]
    result: list[ResearchVariant] = []
    for name, ignored, description in specs:
        result.append(
            ResearchVariant(
                name=name,
                parameter_overrides={},
                ignored_rules=frozenset({ignored}) if ignored else frozenset(),
                description=description,
            )
        )
    return result


def _robustness_variants() -> list[ResearchVariant]:
    grid: dict[str, Sequence[float]] = {
        "adx_threshold": (18.0, 20.0, 22.0, 24.0, 26.0),
        "ema_separation_min_atr": (0.05, 0.10, 0.15, 0.20),
        "confirmation_min_body_ratio": (0.30, 0.35, 0.40, 0.45, 0.50),
        "confirmation_close_location_pct": (0.20, 0.25, 0.30, 0.35, 0.40),
        "confirmation_max_range_atr": (1.25, 1.50, 1.75, 2.00),
        "confluence_distance_atr": (0.15, 0.20, 0.25, 0.30, 0.35),
        "sr_zone_atr": (0.05, 0.10, 0.15, 0.20),
        "minimum_stop_distance_atr": (0.60, 0.70, 0.80, 0.90, 1.00),
        "maximum_stop_distance_atr": (1.25, 1.50, 1.75, 2.00),
        "minimum_room_to_opposing_sr_r": (1.00, 1.25, 1.50, 1.75, 2.00),
    }
    result = [ResearchVariant(name="baseline", parameter_overrides={}, description="V2 baseline")]
    for parameter, values in grid.items():
        for value in values:
            result.append(
                ResearchVariant(
                    name=f"{parameter}={value:g}",
                    parameter_overrides={parameter: float(value)},
                    description=f"One-axis robustness sweep for {parameter}",
                )
            )
    return result


def build_ablation_report(candidate_report: dict[str, Any]) -> dict[str, Any]:
    rows = candidate_report["rows"]
    config = StrategyTunablesConfig()
    results = []
    for variant in _ablation_variants():
        selected = _selected_rows(rows, variant, config)
        results.append({
            "variant": variant.name,
            "description": variant.description,
            "ignored_rules": sorted(variant.ignored_rules),
            "metrics": _summarize_rows(selected),
        })
    return {
        "research_type": "STRATEGY_A_V2_RULE_ABLATION",
        "source_candidate_rows": len(rows),
        "objective": "Compare one-rule-at-a-time removals using futures T1-first-hit labels; no variant changes production configuration.",
        "results": results,
    }


def build_robustness_report(candidate_report: dict[str, Any]) -> dict[str, Any]:
    rows = candidate_report["rows"]
    config = StrategyTunablesConfig()
    results = []
    for variant in _robustness_variants():
        selected = _selected_rows(rows, variant, config)
        results.append({
            "variant": variant.name,
            "parameter_overrides": variant.parameter_overrides,
            "metrics": _summarize_rows(selected),
        })
    return {
        "research_type": "STRATEGY_A_V2_PARAMETER_ROBUSTNESS",
        "source_candidate_rows": len(rows),
        "note": (
            "Only one entry-filter parameter is changed at a time. Trigger buffer, "
            "structural stop buffer, chase, targets, and lifecycle rules remain frozen "
            "because changing geometry requires re-labelling future paths."
        ),
        "results": results,
    }


def _variant_catalog() -> list[ResearchVariant]:
    variants: dict[str, ResearchVariant] = {}
    for variant in _ablation_variants() + _robustness_variants():
        variants.setdefault(variant.name, variant)
    return list(variants.values())


def _walk_forward_folds(
    dates: Sequence[str],
    *,
    train_sessions: int,
    test_sessions: int,
) -> list[tuple[list[str], list[str]]]:
    if train_sessions < 1 or test_sessions < 1:
        raise ValueError("train_sessions and test_sessions must be positive")
    unique = sorted(set(dates))
    folds: list[tuple[list[str], list[str]]] = []
    cursor = 0
    while cursor + train_sessions + test_sessions <= len(unique):
        train = unique[cursor: cursor + train_sessions]
        test = unique[
            cursor + train_sessions:
            cursor + train_sessions + test_sessions
        ]
        folds.append((train, test))
        cursor += test_sessions
    return folds


def _research_score(metrics: dict[str, Any], *, min_triggers: int) -> tuple[float, float, int] | None:
    count = int(metrics.get("triggered_rows") or 0)
    expectancy = metrics.get("mean_t1_first_hit_r")
    drawdown = metrics.get("max_drawdown_t1_first_hit_r")
    if count < min_triggers or expectancy is None:
        return None
    # Higher expectancy, shallower drawdown, then larger sample.
    return (
        float(expectancy),
        float(drawdown or 0.0),
        count,
    )


def build_walk_forward_report(
    candidate_report: dict[str, Any],
    *,
    train_sessions: int = 120,
    test_sessions: int = 40,
    min_train_triggers: int = 8,
) -> dict[str, Any]:
    rows = candidate_report["rows"]
    config = StrategyTunablesConfig()
    variants = _variant_catalog()
    by_date: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_date.setdefault(row["date"], []).append(row)
    folds = _walk_forward_folds(
        sorted(by_date),
        train_sessions=train_sessions,
        test_sessions=test_sessions,
    )

    fold_reports: list[dict[str, Any]] = []
    combined_selected_test_rows: list[dict[str, Any]] = []
    combined_baseline_test_rows: list[dict[str, Any]] = []

    for index, (train_dates, test_dates) in enumerate(folds, start=1):
        train_set = set(train_dates)
        test_set = set(test_dates)
        train_rows = [row for row in rows if row["date"] in train_set]
        test_rows = [row for row in rows if row["date"] in test_set]

        scored: list[tuple[tuple[float, float, int], ResearchVariant, dict[str, Any]]] = []
        train_metrics_by_variant: dict[str, dict[str, Any]] = {}
        for variant in variants:
            metrics = _summarize_rows(_selected_rows(train_rows, variant, config))
            train_metrics_by_variant[variant.name] = metrics
            score = _research_score(metrics, min_triggers=min_train_triggers)
            if score is not None:
                scored.append((score, variant, metrics))

        if scored:
            scored.sort(key=lambda item: item[0], reverse=True)
            chosen = scored[0][1]
            selection_reason = "BEST_ELIGIBLE_TRAIN_EXPECTANCY_THEN_DRAWDOWN_THEN_SAMPLE"
        else:
            chosen = next(variant for variant in variants if variant.name == "baseline")
            selection_reason = "NO_VARIANT_MET_MINIMUM_TRAIN_TRIGGER_COUNT_BASELINE_USED"

        baseline = next(variant for variant in variants if variant.name == "baseline")
        chosen_test = _selected_rows(test_rows, chosen, config)
        baseline_test = _selected_rows(test_rows, baseline, config)
        combined_selected_test_rows.extend(chosen_test)
        combined_baseline_test_rows.extend(baseline_test)

        fold_reports.append({
            "fold": index,
            "train_start": train_dates[0],
            "train_end": train_dates[-1],
            "test_start": test_dates[0],
            "test_end": test_dates[-1],
            "chosen_variant": chosen.name,
            "selection_reason": selection_reason,
            "chosen_train_metrics": train_metrics_by_variant[chosen.name],
            "chosen_test_metrics": _summarize_rows(chosen_test),
            "baseline_test_metrics": _summarize_rows(baseline_test),
        })

    return {
        "research_type": "STRATEGY_A_V2_CHRONOLOGICAL_WALK_FORWARD",
        "train_sessions": train_sessions,
        "test_sessions": test_sessions,
        "min_train_triggers": min_train_triggers,
        "fold_count": len(fold_reports),
        "selection_objective": (
            "Among variants with enough triggered train rows: maximize mean "
            "T1-first-hit R, then prefer shallower drawdown, then larger sample. "
            "The chosen variant is evaluated only on the following chronological test block."
        ),
        "important_limitation": (
            "Decision rows can overlap and are not portfolio trades. Walk-forward "
            "results are rule-quality evidence, not a deployable backtest."
        ),
        "folds": fold_reports,
        "combined_out_of_sample": {
            "selected_variants": _summarize_rows(combined_selected_test_rows),
            "baseline": _summarize_rows(combined_baseline_test_rows),
        },
    }


def _research_thresholds(config: StrategyTunablesConfig) -> dict[str, Any]:
    names = (
        "adx_threshold",
        "ema_separation_min_atr",
        "confirmation_min_body_ratio",
        "confirmation_close_location_pct",
        "confirmation_max_range_atr",
        "confluence_distance_atr",
        "sr_zone_atr",
        "trigger_buffer_atr",
        "trigger_validity_bars",
        "maximum_chase_atr",
        "structural_stop_buffer_atr",
        "minimum_stop_distance_atr",
        "maximum_stop_distance_atr",
        "minimum_room_to_opposing_sr_r",
        "t1_r",
        "runner_target_reference_r",
        "entry_session_start",
        "entry_session_end",
        "forced_exit_time",
    )
    return {name: getattr(config, name) for name in names}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run_research(
    db_path: Path,
    *,
    sessions: int,
    source: str,
    output_dir: Path,
    train_sessions: int,
    test_sessions: int,
    min_train_triggers: int,
) -> dict[str, Any]:
    candidates = build_candidates(
        db_path,
        sessions=sessions,
        source=source,
    )
    ablation = build_ablation_report(candidates)
    robustness = build_robustness_report(candidates)

    dates = sorted({row["date"] for row in candidates["rows"]})
    effective_train = train_sessions
    effective_test = test_sessions
    if len(dates) < train_sessions + test_sessions and len(dates) >= 4:
        effective_test = max(1, len(dates) // 4)
        effective_train = max(1, len(dates) - effective_test)
    walk_forward = build_walk_forward_report(
        candidates,
        train_sessions=effective_train,
        test_sessions=effective_test,
        min_train_triggers=min_train_triggers,
    )

    paths = {
        "candidates": output_dir / "strategy_a_research_candidates.json",
        "ablation": output_dir / "strategy_a_rule_ablation.json",
        "robustness": output_dir / "strategy_a_parameter_robustness.json",
        "walk_forward": output_dir / "strategy_a_walk_forward.json",
    }
    _write_json(paths["candidates"], candidates)
    _write_json(paths["ablation"], ablation)
    _write_json(paths["robustness"], robustness)
    _write_json(paths["walk_forward"], walk_forward)

    baseline_variant = ResearchVariant("baseline", {})
    baseline_metrics = _summarize_rows(
        _selected_rows(
            candidates["rows"],
            baseline_variant,
            StrategyTunablesConfig(),
        )
    )
    summary = {
        "research_type": "STRATEGY_A_V2_RESEARCH_SUMMARY",
        "source": source.upper(),
        "sessions_found": candidates["sessions_found"],
        "directional_rows": candidates["directional_rows"],
        "baseline_metrics": baseline_metrics,
        "walk_forward_folds": walk_forward["fold_count"],
        "outputs": {key: str(value) for key, value in paths.items()},
        "production_thresholds_changed": False,
        "market_data_written": False,
        "broker_called": False,
    }
    _write_json(output_dir / "strategy_a_research_summary.json", summary)
    return summary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only Strategy A historical research, ablation, robustness, and walk-forward harness"
    )
    parser.add_argument("--db-path", type=Path, default=_default_db_path())
    parser.add_argument(
        "--sessions",
        type=int,
        default=0,
        help="Latest N cached sessions to research; 0 means all available sessions.",
    )
    parser.add_argument(
        "--source",
        choices=("BREEZE", "KITE", "LIVE", "MIXED"),
        default="BREEZE",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data") / "strategy_a_research",
    )
    parser.add_argument("--train-sessions", type=int, default=120)
    parser.add_argument("--test-sessions", type=int, default=40)
    parser.add_argument("--min-train-triggers", type=int, default=8)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.sessions < 0:
        raise ValueError("--sessions must be 0 or greater")
    summary = run_research(
        args.db_path,
        sessions=args.sessions,
        source=args.source,
        output_dir=args.output_dir,
        train_sessions=args.train_sessions,
        test_sessions=args.test_sessions,
        min_train_triggers=args.min_train_triggers,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
